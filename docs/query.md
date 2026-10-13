# `--query`, `--explain` and the surface: a design, and the build of its first slices (Q1, Q2)

> **Status: Q0 (the plan in one `Inputs`), Q1 (`--query`, `--rows`) and Q2 (`--explain`) are built; the surface of Q3 (the docs and the MCP check) is a design, most of it done by the generators.** This document follows the rules of `docs/numbers.md` (decisions with their reasons, the gates fixed before
> the build, claims that are measured and assumptions that are listed). Its twin is [`docs/readers.md`](readers.md) (standard input, JSON lines, the reader interface, the outputs). Sections 0 to 13 are the design as it was accepted on 2026-10-07
> ("go with the recommendations"); **section 14 is the build of Q1 and section 15 the build of Q2: what each is, the readings of the design that had to be chosen (each the smallest reading, each recorded), the gates and their numbers, and what is not measured.** Where section 14 or 15
> and an earlier section differ, the later section is what the program does. The prototype and its examples are now the reference front end of the tests (`tests/conformance/query_ref.py`, `query_cases.py`), which share no code with the tool.
>
> **What was measured when the design was written:** the real `table` (main, `3084478`, built with the pinned compiler) on randomly generated tables and plans; 800 cases (1,556 runs, as JSON and as CSV) of the gate gave **0 differences** between the flag form
> and the translated query; the prototype's **26 mutants are all killed**; and the generator found **one bug in `table` itself and three places where the flags' own rules differ** (section 12), which is what a gate is for.
> The prototype was a translator written in Python, so *nothing there measured the speed or the memory of a cancho parser*; section 14 does, for the parser that was built.

## 0. The short version

| decision | choice | why, in one line |
|---|---|---|
| what `--query` is | **a translator from a small SQL subset to the flags the tool already has**; the existing parsers then fill the one `Query`, so there is no second plan to keep equal | the equivalence the design asked for holds by construction for meaning, and the gate checks the writing (quoting and escaping) |
| the subset | `select` (columns, `*`, `count(*)`, `sum`, `min`, `max`, `mean`, `count(distinct c)`) `from` (`'file'` or `stdin`) `where` (`and` of comparisons, `contains`, `in`) `group by` `order by` `limit n offset m`. **No `or`, no parentheses, no aliases, no expressions, no joins, no `like`, no null** | exactly what the engine has; every other SQL word is a tagged refusal that says what to write instead |
| types | **`cast(x as int)` or `x::int`**, `dec(S)`, `float`: the engine's three types, named | one meaning, two spellings agents already type |
| names | bare ASCII identifiers; **`"double quoted"`** for anything else (spaces, commas, dots, keywords, non-ASCII); **`#3`** a position; values are **`'single quoted'`** or numbers | SQL's own convention; a column called `select` is `"select"` |
| aliases | **no** (`as` is refused). The engine has no renamed column; a feature with no flag form has no place in the gate | |
| `limit n offset m` | `--limit n --from m`: **a page**, as the flags are, with `truncated` and `next` | one meaning of limit |
| `select *` alone | needs one new flag, **`--rows`** (rows, all columns, no condition): today `table FILE` is the shape, and there is no way to ask for every row without a condition | a small gap the front end found |
| errors | `query.syntax` (offset, what was expected), `query.unsupported` (the feature, what to write), `query.group-mismatch`, `query.too-long`; all exit 2 | as `where.syntax` does |
| the gate | **a random plan rendered twice (flags, SQL), both run on the same table: same stdout, same stderr, same exit, refusals included**; prototype run: 0 differences in 1,556 runs; 26 of 26 mutants killed | section 8 |
| `table explain` | **`--explain`, a flag** (not a subcommand): the resolved plan, columns with positions and types, bounds, the work class, the refusals that are certain without reading data and the ones that are possible; it reads the first chunk of the file for the header and nothing else, and nothing from standard input | section 9, built as section 15 says. Decided (section 13, 1): the flag |
| surface | `introspect` and `skill` stay generated from the flag table and the rule table; the grammar's lexical tables (keywords, functions, types) are static tables the lexer and `introspect` both read; the examples in the skill are tested by parsing them | section 10 |

## 1. Why a translator, and what it costs

A front end can compile a query to a plan in two ways. It can fill the `Query` itself (names, conditions, literals, aggregates, order keys), or it can *write the flags* and let the code that reads flags fill it. The first is the
shape `docs/filter.md` anticipated ("a later `--query` string would compile to the same one"), and it needs a second implementation of everything a flag does: the name resolution, the `:dec(S)` scales, the literal checks, the `mean` rules. The second needs
none of it, and **its worst case is a mistake in writing a flag**: a name quoted wrongly, an escape missed. That is a class of bug a random test finds quickly, and the spike found three real ones in the flags themselves (section 9).

What it costs:

* **Errors that the flags' parsers would raise have to be raised by the front end, at offsets in the query.** `where.syntax` carries an offset into the *translated* expression and the expression itself; a query user must be pointed at the query. So the front end checks, before it translates, what `--where` checks of a literal: an `int` literal is an integer that fits 64 bits, a `dec(S)` literal has at most S fractional digits and fits 18 digits scaled, and at most 64 conditions and 1,024 values. It raises `query.syntax` for them at the literal. The gate compares the two refusals *as a class* (a literal that does not fit its column is `where.syntax` in one form and `query.syntax` in the other; they are one refusal written for two front ends) and everything else byte for byte.
* **A repair in a refusal that came from a query is the flag form** (`argv` that is a runnable `table ...`), corrected. It is valid and exact, and says so in its hint. A repair in the query form needs the byte span of each name in the query kept beside the plan, which is Q-later (section 11).
* **`detail.flag` of an engine refusal** names the clause (`query:select`, `query:where`, `query:group by`, `query:order by`) so that a reader of the refusal knows where in the query to look.

## 2. The grammar

```ebnf
statement = "select" items [ "from" source ] [ "where" conds ] [ "group" "by" columns ]
            [ "order" "by" keys ] [ "limit" nat [ "offset" nat ] | "offset" nat ] [ ";" ] ;
items     = "*" | item { "," item } ;
item      = column | aggregate ;
aggregate = "count" "(" "*" ")"
          | "count" "(" "distinct" typed ")"
          | ( "sum" | "min" | "max" ) "(" typed ")"
          | "mean" "(" typed [ "," nat ] ")" ;
source    = string | "stdin" ;
conds     = cond { "and" cond } ;
cond      = typed cmp value | column "contains" value | typed "in" "(" value { "," value } ")" ;
cmp       = "=" | "!=" | "<>" | "<" | "<=" | ">" | ">=" ;
keys      = key { "," key } ;
key       = ( typed | aggregate ) [ "asc" | "desc" ] ;
columns   = column { "," column } ;
typed     = column [ "::" type ] | "cast" "(" column "as" type ")" ;
type      = "int" | "float" | "dec" "(" nat ")" ;            (* dec: 0 to 18 *)
column    = ident | quoted | "#" digits ;
value     = string | number ;
ident     = ( letter | "_" ) { letter | digit | "_" } ;      (* ASCII; not a reserved word *)
quoted    = '"' { char - '"' | '""' } '"' ;                  (* never a keyword, never a position; not empty *)
string    = "'" { char - "'" | "''" } "'" ;                  (* a backslash is a backslash *)
number    = [ "+" | "-" ] ( digits [ "." [ digits ] ] | "." digits ) [ ( "e" | "E" ) [ "+" | "-" ] digits ] ;
nat       = digits ;
```

**Lexical.** Space, tab, CR and LF separate tokens. **Keywords are case-insensitive** (`SELECT`, `Select`); **names are case-sensitive**, as in `--select`. No comments (`--` and `/* */` are a syntax error), no `+` or `||` or `%` (`query.unsupported`, "expression").
A query is at most **4,096 bytes** (`query.too-long`, exit 2: nothing was read).

**Reserved words.** A bare identifier may not be one of: `select from where group order limit offset by and in contains asc desc as cast`. **And not one of the words the grammar will need later**: `join inner left right full cross on using having union intersect except like ilike between is not or case when then else end null true false distinct over with all any exists`.
A query that names such a column writes it `"like"`; reserving them *now* is what lets a later version use them without breaking a query that is valid today. `count sum min max mean` are functions only before `(` (a column called `count` is `count`); `int float dec` are types only after `as` or `::`; `stdin` is a source only after `from`.

**Not in the subset, each a `query.unsupported` that names the feature and what to write** (the second column is the hint):

| feature | what the hint says |
|---|---|
| `or`, parentheses | only `and`; several values of one column are `col in ('a', 'b')` |
| `like`, `ilike` | `col contains 'text'` finds a substring; there are no patterns |
| `between`, `not`, `is null`, `null`, booleans | write two conditions; there is no null (an empty cell is the empty text: `= ''`) |
| `as` (alias) | a column keeps its header name, an aggregate its name (`sum:bytes`); there is no renaming |
| `count(col)` | `count(*)` counts rows, `count(distinct col)` counts values: `count(col)` would count the empty cells too |
| `col = other_col` | a condition compares a column with a value |
| an expression, a function, `cast` in the select list | a type says how a column is *read* in a condition, a sort or an aggregate; selecting returns its text |
| `join`, a subquery, `union`, `having`, `select distinct` | one input and one statement; `group by` for distinct rows |
| `order by` two keys of a grouping, by a position (`order by 2`), by `dec` or `float` | `--sort` takes one output column by name; numeric order is `int` only (`docs/sort.md`) |
| `group by` a typed column | built (N5): `--group x:dec(2)`, `x:float`, `x:int` group by value, written back at the scale, in numeric order (`docs/numbers.md`) |

**What the grammar does not say but the translator checks (`query.group-mismatch`, with a corrected query as the repair's hint):**

* a query with an aggregate or a `group by` is a **grouping**, and its select list is **the `group by` columns in the same order, then the aggregates** (that is what the output is, `docs/filter.md`), with at least one aggregate (a grouping always counts something: `count(*)`);
* an aggregate in `order by` must be one in the select list; a column there must be a `group by` column;
* with no `group by` and no aggregate it is **rows**: select list or `*`.

## 3. The mapping: query -> flags -> plan

| the query says | the flag it becomes | where it lands in the plan (`query.cho`) |
|---|---|---|
| `select a, b` | `--select a,b` | `meta[0]` select names, in `names` |
| `select *` with a condition or an order | nothing (`--where` or `--order-by` already return every column) | |
| `select *` alone | **`--rows`** (new) | mode 1 |
| `where c1 and c2` | `--where "c1 and c2"` | `conds` (7 ints each), `lits`, `ltext`, a name per condition |
| `x::int`, `cast(x as int)` | `x:int` | the condition's type 1; an aggregate's type 1 |
| `x::dec(2)` | `x:dec(2)` | type 2 + 2 |
| `x::float` | `x:float` | the float type |
| `x <> v` | `x != v` | op 1 |
| `group by a, b` | `--group a,b` | `meta[2]` group names |
| `count(*)` | `count` in `--agg` | `aggs`: function 0, no name |
| `count(distinct x)` | `distinct:x` | function 4 |
| `sum(x)`, `min`, `max` | `sum:x`, `min:x`, `max:x` | functions 1, 2, 3 |
| `mean(x::dec(2), 3)` | `mean:x:dec(2)@3` | the mean field: 1 + 3 |
| `order by k [desc]` (rows) | `--order-by k` or `-k`, `k:int` for `cast(k as int)` | `meta[4]` order keys, flags 1 and 2 |
| `order by count(*) desc` (grouping) | `--sort -count`; `sum(x)` is `--sort -sum:x` | *not in `Query`* (read by `body`) |
| `limit n offset m` | `--limit n --from m` | *not in `Query`* (read by `body`) |
| `from 'f.csv'` | the operand `f.csv` | the source |
| `from stdin` | no operand (standard input, stage R1 of `docs/readers.md`) | |

**`--sort`, `--top`, `--limit` and `--from` are not in `Query`**; they are values `body` reads from the flags. Stage Q0 puts all of these (and the plan flags) behind one function that answers where each value comes from (flags or query) with **no change of output**; that is the only change to the existing code the front end needs.

**Escaping is per flag, and the flags disagree** (found by the gate, section 9). A column name is written:

| in | rule |
|---|---|
| `--select`, `--group` | a backslash before a comma, a backslash and a leading `#` |
| `--order-by` | the same, and before a `:` and a leading `-`; **an at-sign cannot be escaped** (`args.bad-value`) |
| `--agg` | the same as `--select`, and before a `:` and an `@` (they start a type and a scale) |
| `--sort` | none: the key is an *output label*, the header's name; **a name that starts with `-` can only be written descending** (`--sort --x` for a header `-x`; `--sort -x` reads descending `x`), and a position is not a label |
| `--where` | `'quoted'` with `''` for a quote, except a plain identifier (bare); a position is `#3` |

A name that is a position is `#3` in all of them. These are in the translator as one function each, and each has its mutants (section 8).

## 4. Worked examples

On the README's five-line `orders.csv` (plus `prices.csv`, `readings.csv`, `odd.csv`, in `tests/conformance/query_cases.py`). Every flag form below was run by the real `table`; the prototype translates every query to
the flags shown (the canonical form quotes a name in `--where` only when it is not a plain identifier, and always writes numbers bare and strings quoted).

| # | query | flags |
|---:|---|---|
| 1 | `select customer, bytes from 'orders.csv' where status = 200` | `--select customer,bytes --where 'status = 200' orders.csv` |
| 2 | `select * from 'orders.csv' where bytes != '' and bytes::int > 100 order by bytes::int desc limit 3` | `--where "bytes != '' and bytes:int > 100" --order-by -bytes:int --limit 3 orders.csv` |
| 3 | `select status, count(*) from 'orders.csv' group by status order by count(*) desc` | `--group status --agg count --sort -count orders.csv` |
| 4 | `select customer, count(*), sum(bytes) from 'orders.csv' where bytes != '' group by customer order by sum(bytes) desc limit 2` | `--where "bytes != ''" --group customer --agg count,sum:bytes --sort -sum:bytes --limit 2 orders.csv` |
| 5 | `select category, sum(price::dec(2)), mean(cast(price as dec(2)), 3) from 'prices.csv' group by category` | `--group category --agg 'sum:price:dec(2),mean:price:dec(2)@3' prices.csv` |
| 6 | `select id from 'orders.csv' where status in (200, 404)` | `--select id --where 'status in (200, 404)' orders.csv` |
| 7 | `select * from 'orders.csv' where customer contains 'Doe' and bytes::int >= 1000` | `--where "customer contains 'Doe' and bytes:int >= 1000" orders.csv` |
| 8 | `select count(distinct customer) from 'orders.csv'` | `--agg distinct:customer orders.csv` |
| 9 | `select min(bytes), max(bytes) from 'orders.csv' where bytes != ''` | `--agg min:bytes,max:bytes --where "bytes != ''" orders.csv` |
| 10 | `select id, customer from 'orders.csv' limit 2 offset 2` | `--select id,customer --limit 2 --from 2 orders.csv` |
| 11 | `select max(temp::float), count(distinct temp::float) from 'readings.csv' where temp != 'NaN'` | `--agg max:temp:float,distinct:temp:float --where "temp != 'NaN'" readings.csv` |
| 12 | `select "select", "a b" from 'odd.csv' where "and" >= '5' order by "order" desc` | `--select 'select,a b' --where "'and' >= '5'" --order-by -order odd.csv` |
| 13 | `select #1, #4 from 'odd.csv' where #3 in (3, 7)` | `--select '#1,#4' --where '#3 in (3, 7)' odd.csv` |
| 14 | `select count(*) from stdin where status = 200` | `--agg count --where 'status = 200'` (standard input, R1) |
| 15 | `select * from 'orders.csv'` | `--rows orders.csv` (**the new flag**) |
| 16 | `SELECT Customer FROM 'orders.csv' WHERE Status = '200'` | refused: `select.unknown-column`, exit 3, as `--select Customer` is (keywords are case-free, names are not) |

What the flag forms print (CSV, run by the real binary):

```console
$ table --where "bytes != '' and bytes:int > 100" --order-by -bytes:int --limit 3 --format csv orders.csv      # 2
id,customer,status,bytes
3,"Doe, Jane",200,2048
1,"Doe, Jane",200,512
4,Acme,500,128
$ table --where "bytes != ''" --group customer --agg count,sum:bytes --sort -sum:bytes --limit 2 --format csv orders.csv   # 4
customer,count,sum:bytes
"Doe, Jane",2,2560
Acme,1,128
$ table --group category --agg 'sum:price:dec(2),mean:price:dec(2)@3' --format csv prices.csv   # 5
category,sum:price,mean:price
home,15.70,7.850
office,14.00,7.000
```

## 5. Errors

All are `INVALID_ARGS`, exit 2 (nothing was read), one JSON line with `{code, rule, message, hint, repair, detail}` like every refusal, and a `detail` of the same shape as `where.syntax`'s:

| rule | `detail` | repair |
|---|---|---|
| `query.syntax` | `offset` (0-based byte in the query), `expected` (in words), `query` (the text, to 4,096 bytes), and for a literal that does not fit its column: `column`, `type`, `value` | `choose` when a nearest valid text exists: a keyword typo (`selct`, `form`, `wher`) within two edits of the keyword expected there; `integer` -> `int`, `decimal(2)` -> `dec(2)`, `double` -> `float`; a double-quoted value (`where a = "x"`) -> `'x'`; `==` -> `=`. The options are the flag-form argv of the corrected query. Else `none` |
| `query.unsupported` | `offset`, `feature` (the table in section 2), `query` | `choose` where one is mechanical (`like '%x%'` -> `contains 'x'`; `or` on one column -> `in (...)`), else `none` with the hint |
| `query.group-mismatch` | `offset`, `expected`, `select` (the list as parsed), `group_by` | `choose`: the query with the select list rewritten to the group columns then the aggregates |
| `query.too-long` | `bytes`, `limit` | `none` |
| `args.conflict` (existing) | `flags` | `--query` with any of `--select --where --group --agg --sort --order-by --top --rows`, and `--limit/--from` when the query has `limit/offset`; `from 'x'` with a `FILE` operand |
| the engine's | unchanged | an unknown column or a literal type conflict reaches the engine's rule exactly as the flags would (`select.unknown-column`, `column.unknown`, `column.type-conflict`, ...) |

**Offsets are pinned** by `tests/conformance/query_cases.py` (29 cases in the design, 159 in the build; each points at the token a person should look at), for instance:

```
select a from 'f' where b like '%x%'      unsupported  26  like          (the word)
select a from 'f' where b = c             unsupported  28  column-comparison
select a from 'f' where b::money = 1      syntax       27  a type: int, dec(N) or float
select a from 'f' where select = 1        syntax       24  a column (a name that is a keyword is written "like this")
select a from 'f'; select b from 'f'      syntax       19  the end of the query (one statement)
select a, count(*) from 'f' group by a order by sum(b)    group-mismatch  48
```

**Exit codes are the existing ones**: syntax and group errors 2; an unknown column 3; a limit 8.

## 6. Names, positions, ambiguity, the source

* **A column called `select`:** `"select"`. A quoted name is never a keyword and never a position (`"#3"` is a header that really is `#3`; `#3` is the third column). **A name that needs a backslash in a flag** (a comma, `#`) needs nothing in a query: the translator escapes it per flag (section 3).
* **A non-ASCII or spaced name:** double quotes. A bare identifier is ASCII (`héllo` is `"héllo"`): the rule is one regular expression a reader can check.
* **A dotted name** (`user.name`, a JSON-lines path): `"user.name"`. A bare `a.b` is a syntax error *on purpose*: the dot is kept for `alias.column` when joins come.
* **Values:** `'single quoted'` with `''` for a quote; **a number is bare** (`200`, `-3`, `12.50`, `2.5e1`). A bare word is never a value (`status = ok` is `query.syntax`: "a value: a number or a 'quoted string'"), so a value can never be mistaken for a column. **A number is text unless the column is typed**: `bytes > 100` compares text, exactly as `--where` does, and `--explain` says so in `notes` (section 9). That is a trap the flags already have and the query inherits; it is not made a refusal because the flag form must refuse exactly what the query form refuses.
* **The source.** `from 'path'` (a string, so a path with dots and slashes needs no rule) or `from stdin`. With no `from`: the `FILE` operand, or standard input when there is none. `from` with an operand is `args.conflict`. The path is the operand's: `--root`, `path.*`, `io.*` and the confinement apply unchanged (a query cannot reach outside `--root`).
* **Input format, fields, delimiter, limits, threads, `--format`** are *flags*, not query: they say *how to read and bound*, the query says *what to ask*. `from 'x.jsonl'` does not guess a format ("no guessing"); `--input-format jsonl --fields ...` ([readers.md](readers.md) section 5).

## 7. Room for joins

Nothing here builds a join; the grammar is shaped so that one fits without breaking a valid query:

* `from source { "join" source "on" cond }`: `join inner left right full cross on using` are reserved now (a syntax error as bare identifiers; `query.unsupported: join` where a join would start).
* `source = ( string | "stdin" ) [ [ "as" ] alias ]`, and a qualified column `alias.column`: `as` is reserved, `.` is not part of any bare name today (`a.b` is a syntax error), so `t.col` is free.
* the select list's `*` could become `alias.*`.
* a join needs its own design (`docs/backlog.md` item 9: a hash join with the build side bounded by `--max-state-bytes`); what this document commits to is only that the words and the dot are not given away.

## 8. The equivalence gate

**Gate Q-EQ: the flag form and the query form give identical bytes for every plan.** Fixed now, able to fail, run on every change to the translator or to a flag's parser.

*The generator* (the spike `query_gate.py` of the design, now `tests/conformance/query_gen.py`; in the tool it extends the existing `tests/conformance` plan generators and the `refimpl.py` oracle): a random **table** (4 to 9 columns whose names are drawn from a pool of awkward ones: spaces, commas, quotes, `#3`, colons, at-signs, a backslash, a leading dash, non-ASCII, a dot, `and`, `select`, `like`, `null`, `from`, `group`; two names that are escape-critical in every table; quoted fields with commas, quotes and newlines; empty cells; integers at the edges of 64 bits, decimals, text) and a random **plan** (0 to 3 conditions with `=`, `!=`, `<>`, `<`, `<=`, `>`, `>=`, `in`, `contains`, `:int`, `:dec(2)`, `:dec(3)`, literals that are cells of the table 60 percent of the time; select list, `*`, or a grouping with 0 to 2 group columns and 0 to 2 aggregates of any function; an order key; `limit`, `offset`; half of the picks are awkward names; a position 12 percent of the time). Rendered by **two pieces of code that share nothing**: `flag_form` (hand-written, as the repo's generators render flags) and `query_form` (SQL with random keyword case and whitespace; a source clause half the time).

*The check:* the query is translated, both argvs run on the same file **as JSON and as CSV**, and the exit status, the standard output and the standard error must be byte-identical, refusals included (`value.not-integer` with its row and column, `column.type-conflict`...). The one normalisation is the class above: a literal that does not fit its column is `where.syntax` / `query.syntax`. In the tool the same is also run for `--threads` in {1, 4} and for standard input.

*What it measured (the prototype against the real `table`):*

| run | result |
|---|---|
| 800 cases, seed 21 (22 with no flag form, which is only `select *` alone) | **1,556 runs, 0 differences**; 438 of them refusals (the rest answers): `value.not-integer` 282, `value.not-decimal` 118, `column.type-conflict` 6, the literal class 32 |
| the prototype's mutants (below), 250 cases from up to four seeds, stopping at the first that fails | **26 of 26 killed** (the last two runs, after the generator was tuned, killed all 26; before the tuning, single seeds of 150 to 300 cases left one to three alive: an `in` list whose dropped value never mattered, a colon in an order key, `contains` read as `=`. The tuning is the generator choosing its literals from the table's own cells and forcing escape-critical names into every table: **a gate is as strong as its generator, and this one was found weak by its own mutants**) |
| a 27th, "a number literal written as a quoted string", is *equivalent* (`200` and `'200'` are the same word to the engine) and was removed | |

*The mutants of the prototype (each one defect, each must be killed):* `<>` not turned into `!=`; `desc` ignored; `offset` sent as `limit`; the scale of `mean` dropped; a quote in a value not doubled; a quote in a name not doubled; a backslash, a comma, a leading `#` not escaped in a list; a position written as a name; a colon in an agg name not escaped; the scale of `dec` lost; the last condition dropped; the last value of an `in` dropped; `contains` read as `=`; a colon in an order key not escaped; a leading dash in an order key not escaped; `limit` and `offset` swapped; `limit` off by one; keywords case-sensitive; the group order reversed; the sign of a sort key lost; `count(distinct x)` as `count`; the select list reversed; `sum` read as `min`; the type of a condition lost.

**The mutation plan for the cancho parser** (`scripts/query_mutants.py`, not run in CI, `test_mutants_apply.py` checks each applies): the prototype's 26, ported; plus the lexer (a doubled quote in a string or a name read as the end; a number's sign or exponent; an identifier's character class; the offset of a token one off; `#` and digits; the 4,096 boundary: 4,096 accepted, 4,097 refused; keyword folding for each reserved word: every word of both lists tested as a bare identifier and as a quoted one); the parser (the `and` chain; the end of an `in` list; `cast` and `::` giving the same node; `count(distinct`; the first error reported, not the last; a trailing `;`; two statements); the checks (the group-mismatch conditions one at a time; the 64-condition and 1,024-value ceilings; the literal-fits-its-column checks against `--where`'s, one per type); the source (`from` and an operand; `from stdin`); `--rows` (alone and with a condition). **A differential oracle:** `query_proto.py` (Python), promoted to `tests/conformance/query_ref.py`, **agrees with the cancho parser on 100,000 generated token sequences** (accepted or refused, the same offset and expectation, and when accepted the same flags). Two independent parsers disagreeing is the alarm.

## 9. `--explain`

> **Built as section 15 says** (Q2). Where this section and section 15 differ, the program does what section 15 says: the refusals of a bad plan are the run's own (there is no `refusals.static` and no `runnable`), the plan is spelled out and not as `plan.flags` and `plan.query`, the text form is not built, and the threads are `requested`, `effective` and `reason`.

`table --explain [the flags or --query] [FILE]` answers what the call *would do*, without doing it. **Why a flag and not `table explain`:** the contract's `describe.Tool` describes one command, so a subcommand has no flag table of its own, no input schema in MCP and no entry in `introspect`; a flag appears in all three with no new mechanism, as `--dry-run` does in the writers. (Decided: section 13, 1.)

**What it reads.** It opens the file and reads the first chunk (the 64 KiB `lines` reads) to take the header, and closes it: **no record after the header is framed, split or counted** (gate E2: the bytes read from the file are at most the first chunk plus a header longer than that, and it takes under 20 ms on a 1 GB file). For **standard input it reads nothing** (consuming it would take the first record from the pipeline for no one's gain): the columns are `unresolved`, and only the refusals that need no header are reported. For JSON lines there is no header: the declared `--fields` are the columns, and nothing is read. **A file that does not exist, cannot be read or has a header past `--max-line-bytes` is refused as the run would refuse it** (`io.not-found` exit 3, `limit.header-too-large` exit 8, and so on), because a call that cannot open its input has nothing to explain.

**What it answers** (a `table.v2` document; `scripts/schemas.py` gains the shape; exit 0, `ok: true`; `--format json`, the default, or `text`):

```json
{"ok":true,"command":"table","schema":"table.v2","data":{"explain":{
  "runnable": true,
  "source": {"kind": "file", "path": "orders.csv", "format": "csv", "delimiter": ",", "header_read": true, "columns": 4},
  "plan": {"mode": "groups",
           "flags": ["--where", "bytes:int > 100", "--group", "customer", "--agg", "count,sum:bytes", "--sort", "-sum:bytes", "--limit", "2"],
           "query": "select customer, count(*), sum(bytes) from 'orders.csv' where cast(bytes as int) > 100 group by customer order by sum(bytes) desc limit 2"},
  "columns": [{"name": "customer", "position": 2, "uses": ["group"]},
              {"name": "bytes", "position": 4, "uses": ["where:int", "sum"]}],
  "types": [{"column": "bytes", "type": "int", "uses": ["where", "sum"]}],
  "output": {"columns": ["customer", "count", "sum:bytes"], "format": "json", "paged": true, "limit": 2, "from": 0},
  "work": {"class": "grouped single pass", "reads": "all rows", "state": "groups, up to --max-groups",
           "bounds": {"max_rows": 10000000, "max_line_bytes": 1048576, "max_groups": 100000, "max_distinct": 100000, "max_state_bytes": 67108864},
           "threads_requested": 4, "threads_effective": 4, "threads_reason": null},
  "refusals": {"static": [],
               "possible": ["parse.csv-ragged-row", "parse.csv-bad-quote", "parse.csv-unterminated-quote", "limit.line-too-long", "limit.record-too-large",
                            "limit.too-many-groups", "limit.state-too-large", "value.not-integer", "value.integer-overflow"]},
  "notes": []}},
 "meta":{"version":"0.4.0"}}
```

* **`plan.flags`** is the argv of plan flags: for a query, the translation (the canonical flag form of that query; this is the equivalence gate's oracle and an agent's way to learn the flags); for flags, the plan flags as given in a fixed order. **`plan.query`** is the query when one was given.
* **`columns`** are the columns the plan names, resolved against the header, with their 1-based positions and what each is used for. **`types`** says which columns are read as `int`, `dec(S)` or `float`, and where.
* **`output.columns`** are exactly the columns the answer would have (the grouping's are the group columns, then `count`, `sum:bytes`).
* **`work.class`** is one of: `shape` (count rows), `stream rows` (one record at a time; stops when the page is full), `grouped single pass`, `sorted rows` (holds up to `--max-sort-rows` rows, or a window of `from + limit + 1` when a page or `--top` bounds it). **`work.bounds`** are the limits in force with the flags' names, so an agent knows what the next call can raise and its ceiling (`introspect` has those).
* **`threads_effective`** and the reason (`"standard input has no ranges"`, `"--order-by sorts on one core"`, `"the file is under --parallel-min-bytes"`, `"--from on a selection"`, `"the shape is not split"`). **Same bytes whatever `--threads`**: `explain` is deterministic and does not depend on the number of cores.
* **`refusals.static`** lists, as the full error records a run would print (rule, code, message, hint, repair, detail), what is **certain** without reading data: `query.*`, `where.syntax` (a literal that does not fit its column), `agg.bad-spec`, `column.type-conflict`, `sort.unknown-key`, `args.*`, `input.*`, and, because the header has been read, `select.unknown-column`, `column.unknown`, `select.ambiguous-column`, `column.ambiguous`. **`runnable` is false exactly when `static` is not empty.** **`refusals.possible`** lists the rules this plan can raise *depending on the data*: `parse.csv-*`, `value.*` for each typed read the plan has, `limit.*` for each bound it uses.
* **`notes`** are advice, not refusals: `"bytes is compared as text in 'bytes > 100'; write bytes::int to compare numbers"`, `"standard input: about 70 MB/s on macOS"`, `"order by on 1,000,000 rows holds them all; add limit or raise --max-sort-rows"`.

**The gate of this section (E1):** for 3,000 generated plans and tables, **every refusal a run raises before it has read a record is in `refusals.static` with the same rule and detail, and `runnable` is true exactly when the run's refusal (if it has one) is a data refusal** (`value.*`, `parse.*`, `limit.*` other than `limit.header-too-large`). That is two implementations of "what can fail before the data" (the plan check and the run) held to each other.

**The text form** (`--explain --format text`, for a person):

```
table would read orders.csv (csv, 4 columns, header read) and answer 2 groups of customer by count and sum:bytes.
  rows kept:  bytes:int > 100            (bytes is read as an exact integer: an empty cell is refused)
  work:       one pass over all rows; keeps up to 100000 groups, 100000 distinct values, 64 MiB of keys
  threads:    4 (the same bytes as 1)
  can fail:   value.not-integer, value.integer-overflow, limit.too-many-groups, parse.csv-ragged-row, ...
  nothing is certain to fail.
```

**Relation to `--dry-run`.** The writers of cancho-tools have `--dry-run`: it reports what an apply *would* change, makes no mutating system call, and **exits 9 (`DRY_RUN`) with `ok: true`** (MCP treats 0 and 9 as an answer). `table` changes nothing, so there is nothing to plan and `--dry-run` is not added: `--explain` is a *report* like `introspect` and **exits 0**. A client that already treats 9 as success needs nothing.

## 10. The surface

**`introspect` and `skill` stay generated from the same tables the program runs on**, and this is how the new pieces keep that property:

| piece | its single source | what is generated from it |
|---|---|---|
| the flags (`--query`, `--rows`, `--explain`, `--input-format`, `--fields`, `--discover`, `--absent`, ...) | the flag table (`flag_table()` in `table.cho`) | `introspect.flags`, the skill's flag list, MCP's input schema |
| the rules (`query.*`, `input.*`, `parse.jsonl-*`, `limit.json-too-many-nodes`, `output.duplicate-column`) | `extra()` | `introspect.rules`, the skill's list, `docs/refusals.md`'s table (`scripts/site.py`) |
| the lexical tables of the grammar: reserved words now and for later, function names, type names, the limits (4,096 bytes, 64 conditions, 1,024 values) | **one static table in the query module** that the lexer reads | `introspect` (a `grammar` object: `reserved`, `reserved_later`, `functions`, `types`, `limits`) |
| the grammar and the examples | the help text of the `--query` flag (the contract has no grammar section, and a flag's help cannot hold `;` or `\|`, so the grammar is written in words and `/`) plus **a list of (query, flags) example pairs in the module** | the skill's "Query" section (below); **a test parses every example of the help and of the skill and requires its flags to be the stated ones** |

The grammar's productions are the one thing that cannot be generated from the code (the parser is hand-written recursive descent). What keeps them honest is that **every example in `introspect` and in the skill is parsed and translated by the tool's own parser in CI** (`scripts/site.py --check` extends to it), and the prototype oracle (section 8) is a second parser the productions are checked against.

**The skill excerpt** (the new section of `table skill`; the lists are generated, the examples are tested):

```markdown
## Query

`--query TEXT` asks in a small subset of SQL and means exactly the flags it translates to (`--explain` prints them). Do not mix it with
--select --where --group --agg --sort --order-by --top.

    select COLUMNS | * | count(*) | sum(c) | min(c) | max(c) | mean(c [, N]) | count(distinct c)
    [from 'FILE' | stdin] [where COND and COND ...] [group by COLUMNS] [order by KEY [desc], ...] [limit N [offset N]]

- COND is `c = 'x'`, `c != 5`, `c <> 5`, `c < 5` (also <= > >=), `c contains 'x'`, `c in ('a', 'b')`. Only `and`: no or, no parentheses.
- Values are 'single quoted' text or bare numbers. Text compares bytewise: `bytes > 100` is text order; write `cast(bytes as int) > 100`
  or `bytes::int > 100` for numbers. Types: int, dec(N), float.
- Names are bare (letters, digits, underscore) or "double quoted" (spaces, commas, dots, keywords); #3 is the third column.
- A grouping selects its `group by` columns first, in the same order, then aggregates; it always has at least one aggregate.
- No aliases, no expressions, no joins, no like, no null: each is refused with the feature named and what to write instead
  (`query.unsupported`). `query.syntax` gives the byte offset and what was expected.
{{generated: reserved words, functions, types, limits}}
{{generated, each tested: examples}}
```

**MCP** (the basic server another agent builds for `table` concurrently, on the design of `cancho-tools/docs/mcp.md`: tool definitions generated from `introspect`). What it needs from this design, each item a property of `introspect`, so nothing is hand-written for `table`:

| needs | provided by | stage |
|---|---|---|
| a `query` property: `{"type": "string", "minLength": 1}` with the flag's help (the grammar in words) as `description` | the `--query` flag, kind `text` | Q1 |
| `explain`: `{"type": "boolean"}`; `rows`: boolean | flags of kind `bool` | Q1, Q2 |
| an optional `file` and a `stdin` string | the operand's `min: 0` and the tool's `stdin: "when FILE is - or absent"` | R1 |
| `input-format` (`enum`: `csv`, `jsonl`, `ndjson`), `absent` (`enum`), `fields` (string), `discover` (boolean), `max-json-nodes` (integer) | flags of kinds `choice`, `text`, `bool`, `nat` | R2 |
| `outputSchema` / `structuredContent` for **every** `data` shape the tool can answer: the shape, the page, the groups, **the explain document and the discover document** | `schemas/table.v2.json` is generated by `scripts/schemas.py` from one `oneOf`; **the two new shapes are added there**, and `test_schema.py`'s validation of every output covers them | Q2, R2 |
| `isError` false for `--explain` | it exits 0 | Q2 |
| `--format` not offered | it is already what the server does (`csv`, `jsonl`, `md` outputs are for pipelines) | |
| the same bytes as the CLI | the server runs the binary; nothing to do | |
| **a limit on a text property's length** (a 4,096-byte query) | not in the contract today: `kind: text` has no maximum. The tool refuses a longer query (`query.too-long`), so the schema is only less informative; a `max` in the flag table is an upstream wish, not a blocker | |

A `from 'x'` in a query reaches the file through the same confinement as the `file` property (the tool's, under `--root`), and the translator never forms a shell command line: names and values go into `argv` elements, so a query cannot add a flag to the call. **The one hazard**: a repair's `argv` is a list, for a runtime that executes it as one; a client that joins it into a shell string must quote it (`docs/refusals.md` already says "argv").

**Docs to write** (the build's, not this PR's): `docs/query.md` becomes the reference (this document minus its decisions, plus the grammar generated from the table); `docs/refusals.md` gets the new rules (generated); `docs/select.md` and `docs/filter.md` get a pointer; the README's examples gain a query version of each one (`scripts/site.py` generates them, so a query that stops translating fails CI); the project page's "what it cannot do" loses "no standard input" and "no JSON lines" when R1 and R2 land and keeps "no joins", "no `or`", "no aliases"; `docs/benchmarks.html` gets the JSON-lines table.

## 11. Stages and gates

Fixed before the build. Each stage ends with every earlier gate green, and with **G6** (`docs/numbers.md`, third revision, with its instruction-count form `gate_regress.py --counter`): the stages Q0 to Q3 touch `body` and add modules and **do not touch `reader.cho`, `scan.cho`, `par.cho`, `engine.cho` or the read loops**, which the stage's `git diff --stat` shows, so G6 holds by construction; a stage that changes any of them measures it.

| stage | what | gates (pass lines, each able to fail) | fail line |
|---|---|---|---|
| **Q0** | `body` reads the plan values (select, where, group, agg, sort, order-by, top, limit, from, rows, the source) through one `Inputs` value filled from the flags; **no change of output** | `scripts/corpus.py` md5 of all 134 plans identical to `main`'s; every conformance test green; `git diff --stat` shows `body` and the new function only | any md5 differs |
| **Q1** | `--query` (module `qparse.cho`: lexer, parser, translator, the static tables), `--rows`, the rules and flags of sections 2 to 5, `introspect` `grammar`, the skill section | **Q1-EQ** the equivalence gate of section 8 in `tests/conformance/test_query.py`: >= 3,000 random plans x {json, csv} x `--threads` {1, 4} (and standard input once R1 is in): identical stdout, stderr, exit, refusals included. **Q1-OFFSETS** the 29 pinned offsets, and one case per `query.unsupported` feature and per `query.group-mismatch` condition. **Q1-ORACLE** the Python parser agrees with the cancho one on 100,000 generated token sequences. **Q1-FUZZ** 10,000 random byte strings and mutated valid queries: no trap, only tagged refusals, exit 2, an offset inside the query. **Q1-LIMITS** 4,096 bytes accepted, 4,097 `query.too-long`; 64 conditions and 1,024 values at and past the ceiling. **Q1-CONFLICT** `--query` with each plan flag: `args.conflict`; `from` with an operand. **Q1-KEYWORDS** every reserved word, now and later, refused bare and accepted quoted, as a column of a table, end to end. **Q1-MUT** the parser's mutants (section 8): all killed or proved equivalent. **Q1-DOC** every example of the help, the skill and the README parses and translates to its stated flags (`scripts/site.py --check`). **Q1-GEN** `introspect` and `skill` regenerate to the committed text. **Q1-G6** | any |
| **Q2** | `--explain` (JSON and text), the explain document in `table.v2` | **E1** static refusals and `runnable` against the run, 3,000 plans (section 9). **E2** the bytes read from the file under `strace` are at most the first chunk (and a header past it), and standard input is not read at all; 1 GB file in under 20 ms. **E3** a missing, unreadable or over-long-header file is refused as the run is (same rule, same exit). **E4** `plan.flags` of a query, run, gives the query's bytes (Q1-EQ from the other side). **E5** `output.columns` equals the columns of the real run's answer, for 3,000 plans. **E6** the document validates against `table.v2` (`scripts/schemas.py --check`, `test_schema.py`). **E7** deterministic: the same bytes twice, and for `--threads` 1 and 16. **E8** the text form: snapshot tests. **E9** mutants: a bound mis-reported, a work class mis-assigned, `runnable` inverted, a static refusal missed, an effective-threads reason wrong, the header read past the first chunk: all killed | any |
| **Q3** | the surface: the docs of section 10, the README's and page's generated examples, the MCP schema check | **S1** `scripts/site.py --check`, `scripts/schemas.py --check`, `scripts/manifest.py --check`. **S2** the MCP generator (cancho-tools' `scripts/mcp.py`) on `table`'s `introspect` produces the properties of section 10 and a `tools/call` with `{"query": ..., "file": ...}` returns, byte for byte, the CLI's output for the same argv; `{"query": ..., "explain": true}` validates against `outputSchema`. **S3** the skill, given to Claude Code headless as in cancho-tools `docs/mcp.md` section 9, answers a question needing a grouping and one needing a refusal's repair, in a recorded run (an evaluation, not a gate) | any |

## 12. What the prototype found (and what the build should do about each)

1. **A bug in `table` today: `--agg max:\#3` reads the *position* 3, not the header called `#3`.** `--select '\#3'` and `--group '\#3'` name the header; the `--agg` parser does not unescape `\#` before it looks for a position. Reproduced on `main` with a header `#3,b,c`: `--agg max:\#3` answers `max:c`. `docs/filter.md` promises "`COL` is a name or `#N`; a name with a comma is written `\,` as in a list". Fix in `agg.cho`'s spec parser (a separate change; the N4 stage is editing that file now, so it is *not* done here). The gate's generator avoids such names in aggregates until it is fixed, and the fix's test is a line of the pool.
2. **The flags' escape rules differ** (table in section 3): `--order-by` escapes a colon and a leading dash and refuses `\@`; `--agg` escapes the colon and the at-sign; `--select` and `--group` neither. The translator has one function per flag, and every one has a mutant. A uniform rule would make the tool simpler to write against; changing it is a change of the flags' behaviour and is not proposed here.
3. **`--sort` takes output *labels*, so a grouping cannot be sorted by a position, and a name that starts with `-` can only be sorted descending**: `--sort -#1` is `sort.unknown-key` (the label is the header's name); for a header `-x`, `--sort -x` is `sort.unknown-key` (it reads descending `x`) and `--sort --x` is the descending sort of `-x`; ascending cannot be written (measured on `main`). The query refuses both cases (`query.unsupported: order-by-position`, `order-by-dash-name`) rather than translate to a different request.
4. **A `where.syntax` carries the offset and the text of the *translated* expression**, so for a query the same fault needs `query.syntax` at the offset in the query: the translator checks literals itself (section 1) and the gate compares them as a class.
5. **`select *` alone has no flag form**, and `--format csv` of every row of a file is "needs `--select`, `--where` or `--group`" today: `--rows` closes it, in the flags and in the query.
6. **A query has no way to set paging separately from `limit`**: `--top` (keep N groups, `group_count` still the whole) has no query form; it stays flag-only, and `--query` conflicts with it only when the query has `limit`. A documented asymmetry, listed so that no one reads the gate as covering it.

## 13. Decisions of the maintainer (2026-10-07: "go with the recommendations")

Every question that was open here is **decided**, on 2026-10-07, **accepted by the maintainer as recommended**. Nothing is open in this document.

1. **`--explain` (a flag) or `table explain` (a subcommand)?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **the flag**: it appears in `introspect`, in MCP's schema and in the skill with no change to the contract. If the subcommand is wanted, `describe.Tool` needs per-command descriptions first (cancho-tools).
2. **Translator (flags) or a second plan builder?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **the translator**, for the reason in section 1; the cost is the repair in flag form and the front end's own literal checks.
3. **Repairs of a query refusal: the flag form (v1) or the query form (needs spans)?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **flag form in v1** (runnable, exact); the query form when someone needs it.
4. **`--rows`: accept the new flag?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **yes**: it is the only way to say "every row" and is a fix to the tool on its own merit.
5. **Fix the `--agg \#` bug now (a separate PR) or with Q1?** (a separate session is on it; `agg.cho`'s handling of `\#` is not touched by the stages here) DECIDED 2026-10-07, accepted by the maintainer as recommended: **now, separately**, with the line in the gate's pool as its test, after the N4 stage lands (the same file).
6. **`or` in `--where` (and so in the query)?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **not in this slice.** It changes the engine (`conds` is a conjunction, evaluated left to right and stopping at the first false one), the typed-cell refusals (what `a:int > 1 or b = 'x'` does with a bad `a` when `b = 'x'` is true) and `explain`; `docs/backlog.md` item 6 already asks for its own design.
7. **Reserve the future words now?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **yes** (list in section 2): a query that is valid today must stay valid.
8. **A `max` on a `text` flag in the contract (query length in MCP's schema)?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **nice to have, upstream, not a blocker.**

## 14. Q1, built: `--query` and `--rows`

**What it is.** `--query TEXT` is read by a front end of its own (`tools/table/qarena.cho`, `qlex.cho`, `qparse.cho`, `qtrans.cho`, `qtext.cho`, `qrefuse.cho`, `qrepair.cho`, `qpath.cho`: 2,830 lines in all, none over 800, and `table.cho` at 1,731) that **writes the flags the query stands for** (section 3), and `body` runs those exactly as it runs the flags it was given. There is no second plan and no second execution path: `body` is now `body` (the flags' values), `query_body` (a query's translation) and `plan_body` (the one that builds the `Query` and runs it; it does not know where its values came from), and the only values it takes beyond `Inputs` are the text of each list and the label of `--sort`, which is now a field of the plan (`query.cho`'s `sort`, so that a query's label does not have to be an argument). `--rows` is new: every row and every column, the one thing the flags could not say (section 12.5).

**What it does with a call.** The query is translated first. If it does not translate it is refused (`query.syntax`, `query.unsupported`, `query.group-mismatch`, `query.too-long`: exit 2, nothing read) with the repair of section 5. If it does, the flags that say the plan too are refused (`args.conflict`: the detail lists `--query` and each offender), and otherwise the plan runs with the translation's values in `Inputs` (`inputs.for_query`). A literal that does not fit the type its column is read as is refused by `--where`'s own parser, and the refusal is turned into `query.syntax` at the literal **of the query**, with `column`, `type` and `value` (the translation is made again with a map of where each literal went: `qrefuse.literal_refusal`).

### 14.1 The readings of the design that had to be chosen (each the smallest reading; a decision is recorded here so that it is read, not discovered)

1. **`--top` beside `--query`.** Section 5 lists `--top` among the flags a query conflicts with and section 12.6 says it "stays a flag" with "no query form". The smallest consistent reading, and the only one under which the 166-plan corpus (whose one `--top` plan has no other spelling) can be run both ways: **`--top` is a flag beside a query**, and it conflicts only with a query that has its own `limit` (both cut the answer). `--limit` and `--from` conflict only with a query that has a `limit` or an `offset`; a flag the query did not use is the flag's own. `--select`, `--where`, `--group`, `--agg`, `--sort`, `--order-by`, `--rows` and **`--report`** conflict always (a report is a form of its own; the query has none: this refuses where section 5 was silent). `from 'x'` or `from stdin` with a `FILE` operand conflicts. `args.conflict`'s `detail.flags` is `--query` then each offender, in that order of the flags' table.
2. **A typed `group by` column.** The grammar (section 2) groups by `columns`, and the table of what the language leaves out says a typed group by column "is built (N5): `--group x:dec(2)`". Both are read as: **`group by x::dec(2)` is `query.unsupported`, feature `cast-in-group`**, with a hint that names the flag (`--group x:int`, `x:dec(N)`, `x:float`). The grammar is not widened.
3. **Engine refusals in the query form.** Section 1 says `detail.flag` names the clause (`query:select`) and a repair is the flag form of the corrected command. Section 8's gate (and the task of this stage) compare the refusals' rule, detail and exit status with the flag form's, so: **`detail` is unchanged, and `detail.flag` is the flag the translation uses** (a reader of `--select` finds the select list), and a repair that offers the invocation back with one argument replaced (`select.unknown-column`'s case variant, `value.decimal-scale`'s larger scale) **has nothing to rewrite in a query call and is `none`**. This is a loss against the flag form, listed in 14.5. The repairs of the front end's own refusals do offer the invocation (14.3).
4. **`group-mismatch`'s detail.** `select` is the text of the select list as it stands in the query (trimmed), and `group_by` the text of the group by columns (empty when there is no group by clause).
5. **The literal that does not fit.** `query.syntax`, offset at the literal in the query, `expected` the words of `--where`'s parser for that type, and `column`, `type` (`int`, `dec(N)`, `float`) and `value`. The ceilings (64 conditions, 1,024 values in one `in`) are counted by the front end and reported at the 65th condition's first byte and the 1,025th value's first byte; the engine's own check of them can no longer fire.
6. **A double-quoted word where a value stands** (`b = "x"`). Section 5 lists it under `query.syntax` with the repair `'x'`; section 2 says a double-quoted word is a *name*, so it is a column and `b = "x"` is `col = other_col`, **`query.unsupported`, feature `column-comparison`**, whose repair is `b = 'x'` (the reference, `query_ref.py`, and the pinned cases agree).
7. **No `grammar` object in `introspect`.** Section 10 asks for one; the contract's `describe.Tool` (pinned) has no slot for it. The reserved words are in section 2 and in `qparse.cho`, the grammar in words is in the help of `--query`, and the examples in the help are run by `tests/conformance/test_skill.py` and `test_query.py`. A `grammar` section is an upstream wish (cancho-tools), as the `max` on a text flag was (section 13, 8).
8. **`from 'path'`.** `toolbox.path.operand` reads its path from an argument of the call, and a query's path is part of one, so `qpath.cho` makes the same checks, rule for rule and in the same order (empty, over 4,096 bytes, a `..` component, absolute: under the root `path.absolute`, elsewhere `path.outside-root`, then `.` and repeated `/` collapsed), and `toolbox.place` opens it beneath the root and refuses a link, as it does an operand. The refusals carry the path and no argument position. `test_query.Source` holds each rule to the operand's.
9. **`--rows`** takes nothing with it: `--select`, `--where`, `--group`, `--agg`, `--order-by` and `--report` are `args.conflict` (the flag is "every row, all columns, no condition"); `--limit`, `--from`, `--format` and the rest apply as to any rows. `select *` with a `where` or an `order by` is `--where` or `--order-by` alone, as section 3 says.
10. **Numbers.** A `limit` or `offset` is at most 18 digits (`query.syntax`, "a non-negative integer of at most 18 digits"); a scale (`dec(S)`, `mean(x, S)`) is 0 to 18 (`a scale from 0 to 18`); `limit 0` reaches the engine's `--limit` check (`args.bad-value`). A query is at most 4,096 bytes, **counted in bytes**.
11. **Comments** (`--`, `/* */`) are `query.syntax`; `+ - / % ||` are `query.unsupported: expression`; `;` ends the statement once and a second one is "the end of the query (one statement)".
12. **`order by` a `dec` or a `float` key.** Section 2's table (and the prototype) refuse it ("numeric order is `int` only (`docs/sort.md`)"); that was true when the design was written and is not now: `--order-by -ratio:float` and `x:dec(S)` have been built since N5 (`docs/numbers.md`), and the corpus orders rows by one. The grammar already says `key = ( typed | aggregate )`, and the instruction was to express what the flags express and no more, so **rows are ordered by `int`, `dec(S)` and `float` keys** (`order by ratio::float desc` is `--order-by -ratio:float`), and `order-by-float` and `order-by-dec` are gone from the features. A grouping is still ordered by one output column, by name (section 12.3).

### 14.2 The gates, written before the code, and what they measured

| gate | what is held | where | result |
|---|---|---|---|
| **Q1-EQ**, corpus | every plan of `scripts/corpus.py` (166 runs of 41 plans over the benchmark files, sequential and threaded with tiny ranges) written as flags and as `--query`: identical status, standard output and standard error | `scripts/query_corpus.py` | see 14.4 |
| **Q1-EQ**, random | random tables (awkward names, quoted fields, empty cells) and plans, rendered as flags and as SQL by two pieces of code that share nothing, JSON and CSV, threads | `tests/conformance/test_query.py` (`Equivalence`, from the spike's `query_gate.py`) | see 14.4 |
| **Q1-OFFSETS** | one case for every place the grammar can fail: rule, byte offset, expectation or feature, select and group_by; the reference front end agrees | `query_cases.py`, `test_query.Refusals` | 159 refusals (99 `query.syntax`, 37 `query.unsupported` of 21 features, 23 `query.group-mismatch`) and 8 literals that do not fit their column |
| **Q1-ORACLE, Q1-FUZZ** | random bytes, token soups and mutated valid queries: no trap, only a rule `introspect` lists, exit 2 and an offset inside the query for a query refusal, and the reference front end (Python, no shared code) agrees on accepted or refused, kind, offset and expectation | `test_query.Fuzz` | see 14.4 |
| **Q1-LIMITS, Q1-CONFLICT, Q1-KEYWORDS, Q1-DOC** | 4,096 and 4,097 bytes; 64 and 65 conditions; 1,024 and 1,025 values; every plan flag beside a query; every reserved word of both lists bare and quoted, in every clause, end to end; the worked examples of section 4 | `test_query.py` | pass |
| **Q1-MUT** | the front end broken in each of the ways of `scripts/query_mutants.py` | `--check` in CI; real runs | see 14.4 |
| **Q1-G6** | no flag path slower than `main`: instructions retired, 1.01 | `scripts/gate_regress.py --counter` | see 14.4 |
| **Q1-GEN** | `introspect`, `skill`, the MCP definition, `docs/refusals.md`, the README and the pages regenerate to what is committed | `scripts/site.py --check`, `scripts/schemas.py --check`, `scripts/mcp.py --check`, `scripts/manifest.py --check` | pass |

### 14.3 The repairs that are mechanical (and the ones that are not built)

A refusal of the front end offers `choose` with the invocation as it was given, the `--query` and its text replaced, in place, by the flags the corrected query stands for, and its `from 'file'` as the operand last; it is offered only when the corrected query translates, and not when it would put a second operand on a call that has one. The corrections: a keyword misspelt within two edits of one that can stand there (`selct`, `form`, `wher`, `limt`, `ordr`: the keywords of the clauses that can still come), a type as SQL names it (`integer`, `decimal(2)`, `double`), `==`, a double-quoted word where a value stands, `like '%x%'`, and a select list that is not the grouping (rewritten as the group by columns, the aggregates it had in order, `count(*)` when it had none, and the aggregate an order by names). **Not built: `or` on one column to `in (...)`** (section 5 lists it): it has none, with the hint.

### 14.4 Measured

**Conditions.** The head measured is `010e98a`'s code (the later commits are documents). The compiler is the pinned `e47d647` on both machines. **Mac**: Apple silicon (arm64), macOS, 16 cores, not quiet: other sessions of the same user were running (load average 5.5 to 9 during the numbers, and one runaway `table` of another session held a core for ten hours). **gram**: Intel i7-1260P (x86-64, Linux 7.0, 4 performance cores with two threads each and 8 efficiency cores), `nice -n 5 taskset -c 0-5` (three physical cores) for the suite, the corpus and the clock gates, the mutants on cores 8 to 15, the extended equivalence and fuzz on cores 6 and 7, so **the box ran several jobs at once** (load average 3 to 5) and the clock numbers carry that. **The instruction counter is not available on gram** (`perf_event_paranoid` is 4 and was not touched: `perf stat -e instructions:u` refuses), so G6 on gram is the clock form with its control.

| what | Mac | gram |
|---|---|---|
| the conformance suite from a clean checkout | 471 tests, OK, 6 skipped, 399 s | 471 tests, OK, 2 skipped, 501 s |
| `scripts/corpus.py`, the md5 of the 166 outputs, `main` against this change | identical (166 lines) | identical (166 lines) |
| **Q1-EQ, the corpus** (`scripts/query_corpus.py --require` all six files: 41 plans, 166 runs, flags and query) | 0 different | 0 different |
| **Q1-EQ, random plans** (3,000 plans, JSON and CSV, one thread and four on tiny ranges: 12,000 runs, seed 11) | 0 different; 3,456 refusals: `value.not-integer` 2,140, `value.not-decimal` 796, `column.unknown` 264, `column.type-conflict` 160, the literal class 96 | the same counts, 0 different |
| **Q1-FUZZ and Q1-ORACLE** (100,000 strings, seed 77: mutated valid queries and refusals, token soups, raw bytes) | 8,074 accepted, 10,997 refused by the engine, 80,929 refused by the front end; **0 disagreements with the reference** (accepted or refused, kind, offset, expectation or feature); no trap; every rule a rule `introspect` lists; 261 s | the same counts |
| the default sizes of CI (150 plans, 1,500 strings): the whole of `test_query.py` | 43 tests, 8 s | |

**G6, the instruction counters (Mac)**, `scripts/gate_regress.py --counter`, 42 cells (21 at 1 thread, 21 at 4), 3 builds each side against `main` `3a534e8` (the three Mac builds of one source are byte-identical, spread 0.0%), bound 1.01: **PASS, 1.000 in 41 cells and 1.001 in one** (`group-count id (1M keys)`, 4 threads, 4,378.1 M against 4,380.8 M). **The first build of Q1 failed it**: 14 of 42 cells over, 1.008 to 1.015, a constant +20 M instructions of 1,520 M on every one-thread cell, with the text of the loops unchanged except for a local removed from the function's prologue; bisected by building `main` with one file replaced at a time (the plan's new field, the flag table, `frame.cho`, `finish.cho` and the new modules: 0; the prologue of `read_file`: +15 M). The loops are now character for character `main`'s (`scripts/stream.py --check`), and the label of `--sort` still travels as the flag does (14.1, item 3 and `frame.prepare`). **A second finding, on a five-row file** (a tiny run, minimum of 5, `/usr/bin/time -l`): the flags path was 7.2% dearer, 33,696,453 instructions against `main`'s 31,425,093, because the last two flags of the table were looked up on every run and `cli.index_of` reads the table from its start entry by entry; now 31,449,758 (+0.08%), shape `table FILE` 29,543,919 against 29,647,318. A `--query` call on the same file is 34,016,530 instructions (+2.6 M: the one lookup of the query's flag, the translation, `inputs.for_query`).

**G6, the clock form (gram)**, the same cells, `--threads 3`, 21 runs, 3 builds each side, the minimum per build, the mean over builds, bound 1.02: **PASS**, ratios 0.866 to 1.012 (one cell, `report types` at 3 threads, with a spread of 44% across builds: the box was not quiet). The same gate on the build before the lookups were avoided (`f1a333f`, the mutants running on other cores): 0.953 to 1.012. **The control, 3 more builds of `main` against the 3 builds of `main`, FAILS** (one cell at 1.027, `group-sum bytes by id (1M keys)` at one thread, and spreads over 4% in eight): the clock form cannot tell a change of less than 3% from the layout of a build, which is why the counter form exists; on gram there is none, and what is claimed for gram is that the clock form passes and its control does not.

**The cost of translating a query**, `scripts/query_cost.py` (the front end of `tools/table` in a loop of its own, `scripts/spikes/query/qbench.cho`; 200,000 translations a run, the minimum of 9 runs less the process start; the median and spread are in the script's output):

| query | bytes | Mac (arm64), ns | gram (x86-64, one P-core), ns |
|---|---:|---:|---:|
| `select a` | 8 | 338 | 400 |
| 5 columns, a condition | 78 | 1,455 | 1,906 |
| a grouping with a sort (example 4) | 124 | 1,458 | 2,055 |
| typed, 3 conditions, an `in` list, an order, a page | 159 | 1,723 | 2,378 |
| the longest the grammar admits | 4,096 | 18,928 | 31,034 |

Load average on the Mac 8.7 at the start and 6.5 at the end (spread across the 9 runs 3% to 6%), on gram 2.3 (spread 3% to 12%). This is the translation alone: a `table --query` call also runs the parsers of the flags it writes and costs 2.6 M instructions more than the flags on a five-row file (above), which is a millisecond or less. **Memory**: the 4,051-byte query of 1,024 values (the most the ceilings allow in 4,096 bytes) peaks at 2.06 to 2.08 MB resident (Mac, `/usr/bin/time -l`, three runs) against 1.77 to 1.85 MB for a small flags run: the working block is proportional to the query (about 14 integers a byte, 460 KB at 4,096 bytes, freed before the file is opened).

**The mutants** (`scripts/query_mutants.py`, run on gram, `nice -n 10`, cores 8 to 15; each is the front end built with one defect and run against `test_query` and `test_inputs`, stopping at the first failure):

| run | result |
|---|---|
| the first, 154 mutants | 131 killed (3 of them by the build refusing a mutant that could not be written to compile); **23 alive**: an exponent written `E` or signed, the aggregate listed before the group column, a position against a quoted name that reads alike, an order by aggregate that differs by its type or scale, the literal refusal's offset, column and value (the equivalence gate compares those as a class and nothing pinned them), a quote in a path, the source path opened by a thread, three repairs, and nine that change nothing a call can see |
| the tests that were missing were written (number literals in every form, five group-mismatch cases, `query_cases.LITERALS`, a path with a quote, threads over a `from`, the repairs that must not be offered), the three mutants that did not build were rewritten, and the second run, **145 mutants** (95 on cores 8-11 and 50 on 12-15): | **143 killed, 2 alive and explained** |
| **explained as equivalent** (`EQUIVALENT` in the script, each with its reason, its site checked by `--check`): 11 | the last error against the first (every production returns at the first failure); the 1,025th value (the engine's ceiling is the same number and gives the same refusal at the same offset); `or` after a condition (the statement's last check refuses it at the same offset); `and`, `in` and `contains` written bare for a column (`--where`'s parser reads them as names there); a refused sort's label (unreachable through a query); `from stdin` (`Inputs` has standard input when there is no operand); a path over 4,096 bytes (does not fit in a query); the root in `full` (`place.open_operand` reads `shown` under a root); the loop bound of `distance` (answers 3 either way) |

### 14.5 Not measured, not built, and what to read twice

* **gram's instruction counts**: the counter is not available there (14.4). The clock form's verdict on gram is a pass whose control fails.
* **Q1-ORACLE** is 100,000 mutated and generated strings, not 100,000 token sequences; the Python reference is the design's prototype kept in step with 14.1, so a mistake in both is possible, which the pinned cases (read one by one) and the worked examples (run) are there to catch.
* **The cost of a query on a large file is the flags'**: after the translation the plan is the flags' plan, and the counters of the flags are G6's. No separate measurement of "`--query` on 1 GB" exists, because there is nothing separate to measure.
* **`or` on one column as `in (...)`** (a repair listed in section 5) is not built. **`--explain`** (Q2) is built in section 15; the `grammar` object (14.1, item 7) is not.
* **Repairs of the engine's refusals in a query call** are `none` where the flag form offers the invocation back (14.1, item 3): `select.unknown-column` with a column that differs in case is the case a person meets first.
* **A query is bytes, not characters**: an offset is a byte (a name in `"double quotes"` with non-ASCII text moves the later offsets by its bytes).

## 15. Q2, built: `--explain`

**What it is.** `--explain` is a flag of the call, beside the flags or the `--query` it describes: `table --explain --group customer --agg count,sum:bytes orders.csv` prints, as one JSON line (`data.explain`, the third member of the `oneOf` of `schemas/table.v2.json`), what that call would do, and does not do it. It is one module, `tools/table/explain.cho` (908 lines; `table.cho` is 1,745), and four changes to what was there: the flag (`spec.cho`), the `Inputs` value (`has_explain`), the shape of `settle` in `table.cho` (a call that explains goes to `explain.settle` before the read loop is entered) and the end of `plan_body` (an explained plan is a document whatever its `--format`). **The read loops (`read_file`, `read_stream`) are not edited** (`git diff --stat` shows `table.cho` changed in `settle`, `plan_body`, the tool's text and an import; `scripts/stream.py --check` passes), and `rules`, exit codes and the authority row are unchanged (`manifests/table.authority.json` differs from main's only in the list of function names, 804 to 821).

**What it does with a call.** The checks of the flags run as they run for a call (`plan_body`: `args.*`, the plan's parsers, `query.*` first for a `--query`); the plan is built. A file is opened under the root as the run opens it (`io.not-found`, `path.*` and the rest, with the run's bytes). The header is then read by `explain.read_head`, which is the header branch of `read_file` and not its loop (a BOM dropped, blank lines skipped, a CR cut, a header with a quoted line break read through its lines, bounded by `--max-line-bytes`), from the same reader the run uses (`toolbox.lines`, one 64 KiB chunk at a time); `frame.prepare` resolves the plan against it, the call the run makes at the same point; and **`finish.explain` writes the refusal of what the header or the plan made impossible, the function the run writes it with**: `select.unknown-column`, `column.unknown`, `select.ambiguous-column`, `column.ambiguous`, `column.type-conflict`, `sort.unknown-key`, `limit.line-too-long`, `limit.header-too-large`, `parse.csv-bad-quote`, `parse.csv-unterminated-quote`. Otherwise the document. **Nothing after the header is framed, split, counted or judged.** A file is read with one `read` of at most 64 KiB (more only for a header that is longer; the document's `bytes_read` says how much); standard input is not read at all.

**An example** (the file of the design's example, `--threads 4 --parallel-min-bytes 0`):

```json
{"explain":{
  "source":{"kind":"file","path":"orders.csv","reader":"file","block_bytes":65536,"format":"csv","delimiter":",","size":58,"header_read":true,"columns":4,"bytes_read":58},
  "plan":{"form":"groups","select":[],
          "where":[{"column":{"ref":"bytes","position":4,"name":"bytes"},"type":"int","op":">","values":["100"]}],
          "group":[{"column":{"ref":"customer","position":2,"name":"customer"},"type":"text"}],
          "aggregates":[{"function":"count","column":null,"type":null,"scale":null},{"function":"sum","column":{"ref":"bytes","position":4,"name":"bytes"},"type":"int","scale":null}],
          "order_by":[],"sort":"-sum:bytes","top":null,"limit":2,"from":0},
  "output":{"format":"json","columns":["customer","count","sum:bytes"]},
  "work":{"class":"grouped single pass","reads":"all rows",
          "bounds":{"max_rows":10000000,"max_line_bytes":1048576,"max_bytes":1048576,"max_groups":100000,"max_state_bytes":67108864},
          "threads":{"requested":4,"effective":4,"reason":null}},
  "refusals":{"possible":["io.read-failed","parse.csv-ragged-row","parse.csv-bad-quote","parse.csv-unterminated-quote","limit.line-too-long","limit.record-too-large","value.not-integer","value.integer-overflow","limit.output-too-large","limit.too-many-groups","limit.state-too-large"]},
  "notes":[]}}
```

and the same plan on a pipe (`--select a --where 'b > 10' --threads 4`): `source` is `{"kind":"stdin","path":"-","reader":"blocks",...,"size":null,"header_read":false,"columns":null,"bytes_read":0}`, every `position` and `name` is null, `output.columns` is null, `work.threads` is `{"requested":4,"effective":1,"reason":"standard input is read on one core"}`, and `notes` has three sentences: that `b` is compared as text with `>` (so 10 comes before 9, and `:int`, `:dec(S)` or `:float` compares numbers), that standard input is not read by `--explain` (its header is unknown, so a name it lacks is not reported), and that `--threads` has no effect on standard input. **This is the place for the note the stdin PR could not express** (`docs/readers.md` 11.2, item 1), and the note has no rule behind it.

### 15.1 The readings of the design that had to be chosen (each the smallest reading, each recorded)

1. **A bad plan is refused as the run refuses it, with the run's bytes and exit status.** Section 9 listed the refusals that are certain in `refusals.static`, with `runnable: false` and exit 0. The instruction for this stage was "same refusals as the real run for a bad plan (the plan is validated)", and it is the smaller thing to build: one function (`finish.explain`) writes the refusal for the run and for `--explain`, so the two cannot differ, and a caller that checks an exit status needs nothing new. So there is **no `refusals.static` and no `runnable`** (they would be an empty list and `true` in every document). Two things differ from the run's bytes, and are normalised by the tests and by nothing else: a repair that offers the invocation corrected carries `--explain` in its `argv` (the invocation had it), and an `args.*` refusal's `position` is the position in the call that was made. The tests put `--explain` last so that positions are the run's.
2. **No `plan.flags` and no `plan.query`.** Section 9 had the plan as an argv of flags (the query's translation, or the flags as given "in a fixed order") and the query's text. The gate asked for here is that the flag spelling and the query spelling of every plan give **the same explain bytes**, and an argv written from the text of the flags cannot do that (`bytes:int>100` and `bytes::int > 100` translate to different texts of the same condition); one written from the plan needs a writer of the flags' escapes, and there are three rules (`--select` and `--group` none, `--agg` the colon and the at-sign, `--order-by` the colon and a leading dash: section 12, item 2). So the plan is **said as the resolved plan**, spelling-independent (`plan.select`, `where`, `group`, `aggregates`, `order_by`, `sort`, `top`, `limit`, `from`, each condition, group, aggregate and order key with its column and its type). `plan.flags` as an argv (an agent's way to learn the flags from a query) is not built.
3. **`columns` and `types` are inside the plan.** Section 9 had two lists beside the plan (`columns`, each with its `uses`; `types`). Here each place the plan names a column has `column: {"ref": how the plan wrote it (a name, `#N` for a position, `\#N` for a header that begins with `#`), "position": 1-based, "name": the header's spelling}` and its `type` (`text`, `int`, `dec(S)`, `float`; an untyped `sum`, `min`, `max` or `mean` of a column is `int`, as the engine reads it), so what the plan reads a column as is said where it is used. `uses` is not built.
4. **`--format` is part of the plan explained, and the document is always JSON.** `output.format` is `json`, `csv` or `text`; an explained `--format csv` plan is a document on standard output (a refusal of it is the run's: a sentence on standard error). The text form of section 9 and its snapshot gate (E8) are **not built**; the instruction for this stage was one JSON line, and `--format text` is the shape's. The plan's own checks apply (`--format csv` with the shape is `args.required-flag`, as in the run).
5. **Standard input is not read; the header of a file is.** As section 9 decided. The instruction said "without consuming it beyond the header"; for a pipe the header cannot be had without consuming the pipe, so the reading that costs a pipeline nothing is none at all, and `source.header_read` and `columns` say so. Nothing that needs the header is reported for it, and `refusals.possible` lists those rules (`select.unknown-column`, `column.unknown`, `column.type-conflict`, `sort.unknown-key`, `limit.header-too-large`, as they apply to the plan) instead.
6. **`work`** is `class` (`shape`, `type report`, `grouped single pass`, `sorted rows`, `stream rows`: section 9 had four, the report is its own class here), `reads` (`all rows`, or `until the page is full` for a stream of rows with a page, which stops at the first matching row past it), `rows_held` (for sorted rows only: the window of `from + limit + 1` rows, or `--top`, or null when the sort holds them all, which is the engine's rule in `read_file`), `bounds` (**only the bounds in force**, with the flags' names and the values the call has: `max_rows` and `max_line_bytes` always, `max_bytes` for a JSON page, `max_groups` and `max_distinct` (only with a `distinct`) for a grouping, `max_sort_rows` for a sort, `max_state_bytes` for a grouping, a sort or the report) and `threads`.
7. **`threads` is `{requested, effective, reason}`, and the design's "same bytes whatever `--threads`" is dropped** (the document says what was requested, so it cannot be the same for 1 and 16; the same flags explain to the same bytes twice, which `test_explain.Spelling` holds). `effective` is the decision `read_file` takes at the header and nothing it learns later: more than one thread only if a file (not a pipe), a rows or groups or report plan (not the shape), not a sort except a whole sort written as csv (`psortable`), not `--from` on a selection, and more than `--parallel-min-bytes` after the header (the size is the file's `fstat`, no read). The reasons are one sentence each. **What it cannot predict**: the parallel code may decline a read at run time (a range whose answer does not fit is read again by the parent; `docs/parallel.md`), and `effective` is then the plan's, not the run's. On Linux, `test_explain.Threads` holds that the run starts a thread (`strace -e clone`) exactly when `effective` is more than one, for the fifteen plans of its table.
8. **`refusals.possible`** is a list of rules, derived from the plan: `io.read-failed` (always), the `parse.csv-*` and `limit.line-too-long`; `limit.record-too-large` unless the shape; `value.not-integer` and `value.integer-overflow` if the plan reads an `int` anywhere (a condition, a group column, an aggregate, an order key; **an untyped `sum`, `min`, `max` and `mean` is an `int` read**), the `dec` and `float` rules for those types, `agg.float-overflow` for a float `sum` or `mean`; `limit.too-many-rows` for a csv answer, a sort or the report; `limit.output-too-large` for a JSON page; the group, distinct, sort and state limits for the plans that hold them; and, for a pipe, the rules that need the header. **Soundness is held by test** (X3: every rule a run raised in 3,000 random plans, and in 25 plans written to raise 21 data rules, is in the list of the explain of the same plan); **that no listed rule is impossible is not claimed** (a rule is listed when the plan reads that kind of thing, not when the data will fail).
9. **`notes`** are three sentences, no more: a text comparison with a number (`<`, `<=`, `>`, `>=` of an untyped column with a literal that reads as a number), a pipe (two sentences: its header is not read, `--threads` does nothing), and a sort that holds all its rows (`--order-by` with no window). The throughput note of section 9 ("about 70 MB/s") is not built: it was a measurement of `getchar`, which is gone (`docs/readers.md` 11.7).
10. **`source` has more than section 9's**: `reader` (`file` or `blocks`) and `block_bytes` (the 64 KiB of both readers), `format` (`csv` or `jsonl`; for JSON lines also `fields`, `absent` and `max_json_nodes`, `delimiter` is null and the header is the declaration, so a pipe's columns are known too: docs/readers.md section 12), `size` (the file's, null for a pipe) and `bytes_read` (what `explain` took from the input: the proof, in the document, of what it read).
11. **No version bump, no new rule.** `schema` stays `table.v2` and `meta.version` stays `0.3.0`: the new document is one more member of the `oneOf` of `data` and is reachable only with the new flag, as Q1's flags and rules were; I found no rule in the contract package that requires another version (the design's example shows `0.4.0`, which would also be a change of every example, fixture and test that prints `meta`, for no reader). The fixture is `tests/conformance/test_explain.py` (`Document.test_the_example_of_the_design` pins the document of the example above, and `harness.validate` validates every explain document of the suite against the schema). A maintainer who wants `0.4.0` changes one string in `table.cho` and the generated documents.
12. **The header is read by a function of its own, not by the loop**: `read_head` is 104 lines that follow the header branch of `read_file` (the order of its checks included: a file with an open quote and no complete header is "no header" for a plan that names columns and an unterminated quote for the shape), because the loops are not edited (G6). The risk of two copies is held by X3: every header case of `test_explain.Header` (an empty file, blank lines, a BOM, CRLF, a header across lines, a bad quote, an open quote, a header past the bound to the byte, three delimiters) is explained or refused with the run's bytes.
13. **The flag's lookup is paid only by a call that has it.** `inputs.from_flags` looks `--explain` up only when an argument begins with it (as `--query` and `--rows` are looked up only when one begins with them): a first version that looked it up beside them made a `--query` call 3.6% dearer in instructions (33,137,903 against 34,315,630 on a five-row file); this one is 33,137,903 against main's 33,138,132.

### 15.2 The gates, written before the code, and what they measured

The gates are in `tests/conformance/test_explain.py` and `scripts/explain_corpus.py`, committed (`6fba76f`) before any code, and failing on `main` (`--explain` was an unknown flag: 166 of 166 corpus runs and the whole conformance file). `TABLE_EXPLAIN_CASES` (default 150), `TABLE_EXPLAIN_FUZZ` (1,200) and `TABLE_EXPLAIN_SEED` set their sizes; the numbers below are from the sizes named.

| gate | what is held | result |
|---|---|---|
| **X1**, corpus | `--explain` of each of the 166 runs of `scripts/corpus.py` (the benchmark files, the json and csv outputs, one thread and tiny ranges), written as flags and as `--query` (the 41 hand-written queries of `scripts/query_corpus.py`): the same bytes, exit 0, and `output.columns` the columns of the real run (a json run's `data.columns`, a csv run's header line) | `scripts/explain_corpus.py --require f1,f2,q,long,wide,num`: **166 runs, 0 different, 166 compared with the real answer's columns** (Mac and gram) |
| **X1**, random | 3,000 random tables (awkward names, quoted cells, empty cells) and plans, each as flags and as a query, as json and csv: the same explain bytes, and the same refusal as a class when the plan is refused | gram: 6,000 pairs, **5,690 explained, 310 refused, 0 different** |
| **X2** | explain never executes: `bytes_read` is 65,536 for a file of any size over it (8 for an 8-byte file), no result is written (one line, `data` has `explain` and nothing else), every cell and record after the header is unlooked at (a file whose rows are all ragged, a quote that never closes, cells unreadable as the type asked: the run refuses with a data rule, the explain describes), a sparse file of 2 GB is explained in under a second, standard input is left unread (`sh -c 'table --explain ...; cat' < file` and from a pipe: `cat` prints every byte), and on Linux `strace` counts the bytes read from the file (at most 65,536 + 4,096) and the `read(0,` calls (none) and the threads started (none) | pass (Mac and gram); gram, `strace` of the call on `build/bench/data.csv` (31.7 MB) and on a 2 GB sparse file, `--threads 4 --parallel-min-bytes 0`: **one `read(fd, ..., 65536) = 65536`, no `clone`** |
| **X3** | the run's refusals: for 3,000 random plans, the plans the run answers are explained (and their columns are the answer's), the plans the run refuses because of the data are explained and the rule is in `refusals.possible`, and the plans it refuses before the data are refused with the run's bytes (status, standard output, standard error); 25 plans written to raise 21 data rules; every header case; the flags' own checks; a file missing, unreadable, outside the root | gram, 3,000 plans: **2,128 answered (all 2,128 with the answer's columns), 698 refused by the data (all with the rule listed), 174 refused before the data (the run's bytes)**; the rest pass |
| **X4** | fuzz: 100,000 mutated, generated and raw query strings, and 50,000 soups of flags, each run with and without `--explain`: no trap, one line, standard error empty, an explain answer when the run answers or fails on the data, and the run's bytes when it is refused first, every refusal a rule `introspect` lists | gram: **100,000 strings: 8,678 explained, 91,322 refused; 50,000 soups: 10,521 explained, 39,479 refused; 0 traps, 0 untagged, 0 disagreements** (671 s on cores 6 and 7, niced, the last run, with the check that every refusal of a soup is a rule `introspect` lists) |
| **X6** | the threads: fifteen plans (pipe, shape, sort, `--from`, the minimum, one thread, csv sort with and without a window) have the decision and the reason the table says; on Linux the run starts a thread exactly when `effective` is more than one | pass (Mac; gram with `strace`) |
| **X-G4** | existing output unchanged: the md5 of the 166 outputs of `scripts/corpus.py` | **identical to main's** (166 lines, `fccb01b6...` of the list), Mac |
| **X-G6** | no existing path slower than `main`: `scripts/gate_regress.py --counter`, 42 cells (21 at 1 thread, 21 at 4), 3 builds each side, bound 1.01 | Mac: **PASS, 1.000 in 34 cells, 0.999 in 3, 1.001 in 4 and 1.002 in 1** (`float min/max by status`, 1 thread: 2,342.9 M against 2,348.2 M, spread 0.2% and 0.6%); the six Mac builds are of two binaries (the builds of one source are byte-identical) |
| **X-MUT** | the explain broken in 98 ways (`scripts/explain_mutants.py`: the header read past the header, standard input read, a bound mis-reported, a class mis-assigned, a threads reason wrong, a rule missing from `possible`, a refusal missed, a csv plan not a document, the flag not found) | `--check` in CI; real runs on gram (15.3): **82 of 98 killed on the first run, 16 alive; after tests were added, 13 of the 14 that could be killed are killed, 3 are equivalent and explained: 95 killed, 3 equivalent, 0 rejected by the build** |
| **X-GEN** | `introspect`, the skill, the MCP definition, `schemas/table.v2.json`, the README and the pages regenerate to what is committed | `scripts/site.py --check`, `schemas.py --check`, `manifest.py --check`, `mcp.py --check`, `seo_check.py`, `cancho fmt --check`: pass |

### 15.3 Measured

**Conditions.** The compiler is the pinned `e47d647` on both machines. **Mac**: Apple silicon (arm64), macOS, 16 cores, **not quiet**: other sessions of the same user were running (load average 6 to 9 during the numbers). **gram**: Intel i7-1260P (x86-64, Linux, 4 performance cores with two threads each and 8 efficiency cores), `nice`, the suite, the corpus, the clock gate and the cost on cores 0 to 5 (the cost on core 0 alone), the mutants on cores 0 to 5 and the 100,000-string fuzz on cores 6 and 7, so the box ran several jobs at once when it was not the only one on it (load average 1 to 6). **The instruction counter is not available on gram** (`perf_event_paranoid` is 4 and was not touched), so G6 on gram is the clock form with its control.

**The cost of an explain** (`scripts/explain_cost.py`: `table --explain --agg count FILE`, the plan of a count of every row, whose run reads the whole file; 31 calls of a process each, the minimum and the median; the run once for comparison; `/usr/bin/time -l` for instructions and resident memory). The load average of the Mac was 6.0 at the start. The document is 990 to 1,003 bytes.

| file | size | Mac explain min / median, ms | Mac run of the same plan, once, ms | gram explain min / median, ms | gram run of the same plan, once, ms | `bytes_read` |
|---|---:|---:|---:|---:|---:|---:|
| 2 columns, 1 row | 16 B | 2.55 / 2.94 (the run: 2.66 min) | | 2.13 / 2.16 (the run: 1.90 min) | | 16 |
| `build/bench/data.csv` | 31.7 MB | 2.65 / 2.95 | 44.7 | 2.08 / 2.14 | 62.7 | 65,536 |
| `build/num/data.csv` | 58.6 MB | 2.79 / 3.02 | 64.9 | 2.04 / 2.10 | 89.5 | 65,536 |
| `build/adv/f2.csv` | 39.6 MB | 2.96 / 3.19 | 55.0 | 2.04 / 2.10 | 73.5 | 65,536 |
| `build/adv/quoted.csv` | 46.7 MB | | | 2.04 / 2.10 | 76.6 | 65,536 |
| `build/adv/long.csv` | 113.9 MB | | | 2.08 / 2.11 | 179.0 | 65,536 |
| `build/adv/wide.csv` | 195.6 MB | 2.62 / 3.06 | 172.1 | 2.10 / 2.13 | 264.3 | 65,536 |
| `build/adv/big.csv` | 1.08 GB | 2.90 / 3.03 | 411.3 | not on gram | | 65,536 |
| a sparse file, header and one row then a hole | 2 GB | 2.73 / 2.99 | | 2.13 / 2.18 | | 65,536 |

Gram's numbers are the second run (a quieter box: load average 2.5 to 3.7 at the start; cores 6 and 7 were running the fuzz). **A first run of the same script on gram, with the mutants running on cores 0 to 5 and the fuzz on cores 6 and 7, gave minima of 2.3 to 4.3 ms and medians of 2.4 to 5.6 ms** for the same files (the minimum of 4.33 ms was the 16-byte file, which the run takes in 5.27 ms once): a gram number under load carries that spread. The Mac was not quiet in either.

**It does not grow with the file**: from 16 bytes to 2 GB the Mac's minimum stays between 2.55 and 2.96 ms and gram's (second run) between 2.04 and 2.13 ms, which is the cost of starting the process (the run of the 16-byte file takes 2.66 ms at the minimum on the Mac and 1.90 on gram), while the run of the same plan goes from 45 ms to 411 ms on the Mac and from 63 to 264 ms on gram. Instructions retired (Mac): 33.22 M for the 16-byte file against 30.91 M for its run (+2.3 M, not decomposed: the document, the lookup of the flag and the read of the header are what the call does more), and 33.28 to 33.52 M for the others. Peak resident memory (Mac, three runs each): **1,818,624 bytes for the 31.7 MB file and for the 1.08 GB file**, 1,966,080 for the run of the 31.7 MB file. On gram under `strace -f -e trace=read,pread64,clone,clone3` with `--threads 4 --parallel-min-bytes 0`, on the 31.7 MB file and on a 2 GB sparse file: one `read(fd, ..., 65536) = 65536` of the file and **no thread started**.

**What a call costs more than the flags'** (instructions retired, Mac, `/usr/bin/time -l`, a five-row file, the minimum and the median of 41 runs of each binary alternated; `main` is `94c39a2`): the shape `table FILE` 29,758,404 / 29,793,562 against 29,771,991 / 29,808,294 (+13.6 k, +0.05%); `--select id --where status=200` 29,924,055 / 29,956,135 against 29,926,128 / 29,950,332; `--group customer --agg sum:bytes` 30,072,931 / 30,103,114 against 30,083,738 / 30,109,714; `--query "select id from 'five.csv' where status = 200"` 33,138,132 / 33,166,626 against 33,137,903 / 33,163,340; and `--explain --select id --where status=200` on the same file 33,265,411 / 33,294,066, which is 3.3 M instructions more than the run of the same plan (29.93 M), about a tenth of a start of the process. **A first count of the five-row run (the minimum of five) showed a mode of 25.0 M instructions for `main` and 25.3 M for this change in one loop and 29.9 M in another run of the same binaries, a bimodality of the count of the process start on this Mac whose cause was not found (it is in `main` as in this change); the numbers above are the 41-run distributions, and the claim is the differences within them.**

**G6, the instruction counters (Mac)**, `scripts/gate_regress.py --counter`, 42 cells (21 at 1 thread, 21 at 4), 3 builds each side against `main` `94c39a2`, bound 1.01: **PASS, ratios 0.999 to 1.002** (34 cells at 1.000, 3 at 0.999, 4 at 1.001 and one at 1.002: `float min/max by status`, 1 thread, 2,342.9 M against 2,348.2 M, a spread of 0.2% and 0.6% among builds that are byte-identical). The loops are not in the diff (`git diff --stat`: `table.cho` changes in `settle` and `plan_body` only, and `scripts/stream.py --check` passes).

**G6, the clock form (gram)**, the same cells, `--threads 3`, 21 runs, 3 builds each side, the minimum per build, the mean over builds, bound 1.02: **PASS**, ratios 0.989 to 1.019 (mean 1.0046; 12 of the 42 cells over 1.01, 10 at or under 1.000; the largest, 1.019, is `group-count path (1000 keys)` at one thread). **The control, 3 more builds of `main` against the same 3 builds of `main`, FAILS**: ratios 0.977 to 1.061 (mean 1.0013), one cell over 1.02 (`group-sum bytes by id (1M keys)` at 3 threads, 1.061, with a build off by 9.7%) and two more with a spread over 4%. The 100,000-string fuzz ran on cores 6 and 7 during the first part of the control and during none of the first run, which is a condition of the control and not an excuse for it. So the clock form cannot tell a change of less than about 6% from the layout of a build or the box's other jobs on gram; the mean of the ratios of the real run is half a percent over 1, the control's a tenth, and the counter on the Mac, which can tell, says 1.000 (0.999 to 1.002) for loops that are not in the diff.

**The conformance suite**: Mac, from the worktree at the head, **513 tests, OK, 10 skipped (the Linux-only ones among them), 474 s** under a load average of 7 to 9; gram, 3 builds apart from the one under test, cores 0 to 5, niced: **513 tests, OK, 2 skipped, 397 s** (the `strace` tests run there).

**The mutants** (`scripts/explain_mutants.py`, run on gram, `nice -n 10`, cores 0 to 5; each is `explain` built with one defect and run against `test_explain`, stopping at the first failure): the first run, 98 mutants: **82 killed, 16 alive** (the boundaries of the threads decision, the rules of a pipe and of the types, a word that is a number, the default of one thread, a quoted header's bound to the byte, the delimiter, the order of a key, the scale of a mean, the page, the bounds in force). The tests that were missing were written, and the 14 that could be killed were run again (one, whose name began with `--`, in a run of its own): **13 killed**. **3 are equivalent and explained** (`EQUIVALENT` in the script, each with its reason, its site checked by `--check`): `as_csv` in the test of a whole sort (implied by there being no limit), `mode != 2` before `row_from != 0` (`row_from` is zero for a grouping), and the type of a count (always 0). So 95 are killed and 3 proved to change nothing; **none was rejected by the build**.

**The fuzz** (X4) and the equivalence over random plans (X1, X3) are in 15.2; the sizes of CI are the defaults of the test (150 plans, 1,200 strings: `test_explain.py`, 42 tests, 9.4 s on the Mac with 4 Linux-only tests skipped).

### 15.4 Not measured, not built, and what to read twice

* **gram's instruction counts**: the counter is not available there (15.3). What is claimed for gram is that the clock form passes (1.019 at the worst) and that its control does not (1.061 at the worst): the pass is not evidence of less than a few percent.
* **The text form** of `--explain` (E8) and `plan.flags` as an argv (15.1, items 2 and 4); `uses` of a column; the `grammar` object (14.1, item 7).
* **`refusals.possible` is sound by test and complete by construction only**: that every rule a run can raise for the plan is listed is held on 3,000 random plans and 25 written ones; that a listed rule can happen is not claimed. A rule of the engine this stage did not know about would be missing until a test found it: the first run of the fuzz found one (`limit.too-many-rows` for the report), fixed before the numbers above.
* **`threads.effective` is the plan's decision at the header**, not the run's: a range the parallel code declines at run time is read by one thread and the document cannot say so.
* **`--explain` on a path that is a FIFO or a device** was not tried.
* **The time of the 2 GB file on gram, and the cost on gram, were measured on a box with other jobs on it** (load average 1 to 6): the minima are stated beside their medians.
* **No measurement of `explain` memory beyond the one process's resident set** (1.8 MB on the Mac) and no measurement of explain under a concurrent writer of the file.
* **The Mac builds of one source are byte-identical, so the three builds of each side of the Mac counter gate are two binaries**; on gram they differ (the clock gate's control is the proof that they do).
