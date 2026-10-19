#!/usr/bin/env python3
"""Mutation check of the bucket of a group key, `--group ts:timestamp@day` (docs/datetime.md section 7, slice T2; gate G-MUT): each mutant is one source file of tools/table with one
deliberate defect, rebuilt, and run against the bucket tests (tests/conformance/test_datetime_t2.py, with the reference datetime_ref.bucket) and the date tests of T1. A mutant is killed
when a test fails or the build refuses it. The files are restored after every mutant.

    python3 scripts/datetime_bucket_mutants.py [name-substring ...]

`--check` only verifies that every site still matches exactly once (no build; CI runs it). See mutlib.py. MUT_SHARD=I/N runs the I-th of N parts of the list (each in a checkout of its
own: a run rebuilds tools/table in place).

The mutants are grouped as the task lists them: the arithmetic of the bucket (floor as truncation, the hour, the day, the month, the year, the era, a date), the UTC day of an instant (the
offset not applied, applied twice, with its sign flipped, before the bucket), the unit (read, accepted when it is finer than the type, a week, a minute, another case), the key (printed
as a bucket, of the base type, the unit ignored), the conflict check (comparing the unit too), `--agg` (a bucket accepted), `--order-by` (a bucket read), the repair (the unit lost or kept
for a type that has not got it), `--explain`, the help and the hint of the query. No mutant is equivalent unless a comment says so.

Mutant "a week is a day" (unit_code answers 2 for the word week) is equivalent: `unit_class` tests the word `week` before it calls `unit_code`, and `unit_code` is called only for a word that
`unit_class` accepted (flags.cho, the repair in dtfail.cho), which `week` never is, so no run reaches the changed answer. It is kept in the list as the one survivor that is proved, and
`--check` still verifies its site.

Not mutated, equivalent: `find_at`'s `i > 0` (an `@` at the start of an item has no type before it: `suffix_in` of an empty word is (0, 0)); the `% 256` of `read` against `& 255` (the
same for a non-negative code).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mutlib  # noqa: E402

# the cheap classes first: a mutant is stopped at the first test that fails, and most are killed by the first few
TESTS = ["test_datetime_t2.Reference", "test_datetime_t2.Spelling", "test_datetime_t2.SameColumn", "test_datetime_t2.Surface", "test_datetime_t2.Repairs", "test_datetime_t2.Buckets",
         "test_datetime_t2.RoundTrip", "test_datetime_t2.Fuzz", "test_datetime_t2.Diff", "test_datetime_t2.Parallel", "test_datetime_t2.Oracle", "test_datetime.Surface", "test_datetime.Grammar",
         "test_rules"]

FIRST_OF = "    // microseconds since 0001-01-01T00:00:00, never negative for a value in range\n    let u = v + 62135596800000000;\n"

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # ---- the arithmetic of the bucket (dtbucket.cho)
    ("floor as truncation: the value is not shifted before the division",
     "dtbucket.cho", "    let u = v + 62135596800000000;\n    if unit == 1 {\n        return u - u % 3600000000 - 62135596800000000;\n    }\n    let day = u - u % 86400000000;\n    if unit == 2 {\n        return day - 62135596800000000;\n    }\n    let (y, m, d) = dtparse.civil_from_shifted(day / 86400000000 + 306);",
     "    let u = v;\n    if unit == 1 {\n        return u - u % 3600000000;\n    }\n    let day = u - u % 86400000000;\n    if unit == 2 {\n        return day;\n    }\n    let (y, m, d) = dtparse.civil_from_shifted(day / 86400000000 + 719468);"),
    ("the hour bucket is half an hour", "dtbucket.cho", "        return u - u % 3600000000 - 62135596800000000;", "        return u - u % 1800000000 - 62135596800000000;"),
    ("the hour bucket is a day", "dtbucket.cho", "        return u - u % 3600000000 - 62135596800000000;", "        return u - u % 86400000000 - 62135596800000000;"),
    ("the hour bucket does not start at minute 0 but at the minute of the epoch", "dtbucket.cho", "        return u - u % 3600000000 - 62135596800000000;", "        return u - (u + 60000000) % 3600000000 - 62135596800000000;"),
    ("the day bucket is half a day", "dtbucket.cho", "    let day = u - u % 86400000000;", "    let day = u - u % 43200000000;"),
    ("the day bucket starts at one in the morning (a Sunday-style anchor)", "dtbucket.cho", "    let day = u - u % 86400000000;", "    let day = u - (u + 82800000000) % 86400000000;"),
    ("the day bucket forgets the shift back to 1970", "dtbucket.cho", "        return day - 62135596800000000;", "        return day;"),
    ("the month bucket is not day 1", "dtbucket.cho", "        return dtparse.days_from_civil(y, m, 1) * 86400000000;", "        return dtparse.days_from_civil(y, m, 2) * 86400000000;"),
    ("the month bucket is the month before", "dtbucket.cho", "        return dtparse.days_from_civil(y, m, 1) * 86400000000;", "        return dtparse.days_from_civil(y, m - 1, 1) * 86400000000;"),
    ("the year bucket is not January 1", "dtbucket.cho", "    return dtparse.days_from_civil(y, 1, 1) * 86400000000;", "    return dtparse.days_from_civil(y, 1, 2) * 86400000000;"),
    ("the year bucket is February", "dtbucket.cho", "    return dtparse.days_from_civil(y, 1, 1) * 86400000000;", "    return dtparse.days_from_civil(y, 2, 1) * 86400000000;"),
    ("the year bucket is the month bucket", "dtbucket.cho", "    return dtparse.days_from_civil(y, 1, 1) * 86400000000;", "    return dtparse.days_from_civil(y, m, 1) * 86400000000;"),
    ("the era of a month bucket is off by a day", "dtbucket.cho", "dtparse.civil_from_shifted(day / 86400000000 + 306)", "dtparse.civil_from_shifted(day / 86400000000 + 305)"),
    ("the unit of the first branch is the day's", "dtbucket.cho", "    let u = v + 62135596800000000;\n    if unit == 1 {", "    let u = v + 62135596800000000;\n    if unit == 2 {"),
    ("a date's month bucket is not day 1", "dtbucket.cho", "            return dtparse.days_from_civil(y, m, 1);\n", "            return dtparse.days_from_civil(y, m, 2);\n"),
    ("a date's year bucket is not January 1", "dtbucket.cho", "        return dtparse.days_from_civil(y, 1, 1);\n    }\n", "        return dtparse.days_from_civil(y, 1, 2);\n    }\n"),
    ("a date's year bucket is its month", "dtbucket.cho", "        return dtparse.days_from_civil(y, 1, 1);\n    }\n", "        return dtparse.days_from_civil(y, m, 1);\n    }\n"),
    ("a date is shifted a day before its civil date is taken", "dtbucket.cho", "dtparse.civil_from_shifted(v + 719468)", "dtparse.civil_from_shifted(v + 719467)"),
    ("a date's unit is read as a month whatever it is (the year bucket is the month)", "dtbucket.cho", "        if unit == 3 {\n            return dtparse.days_from_civil(y, m, 1);", "        if unit >= 3 {\n            return dtparse.days_from_civil(y, m, 1);"),
    # ---- the reading of the key (dtbucket.read)
    ("the bucket is not applied", "dtbucket.cho", "    return (first_of(v, kind, raw / 256), 0);", "    return (v, 0);"),
    ("a cell that is refused is bucketed all the same", "dtbucket.cho", "    if bad != 0 {\n        return (v, bad);\n    }\n    return (first_of(v, kind, raw / 256), 0);", "    return (first_of(v, kind, raw / 256), 0);"),
    ("the unit is the type", "dtbucket.cho", "    return (first_of(v, kind, raw / 256), 0);", "    return (first_of(v, kind, kind), 0);"),
    ("the type is read from the wrong end of the code", "dtbucket.cho", "    let kind = raw % 256;", "    let kind = raw / 256 + 22;"),
    # ---- the UTC day of an instant (dtparse.cho: the same lines as the mutants of the offset in datetime_mutants.py, killed here by the bucket)
    ("the offset is not applied before bucketing (the cell's own day)", "dtparse.cho", "        value = value - offset_us;\n        if value < 0 - 62135596800000000 || value", "        value = value;\n        if value < 0 - 62135596800000000 || value"),
    ("the offset is applied twice before bucketing", "dtparse.cho", "        value = value - offset_us;\n        if value < 0 - 62135596800000000 || value", "        value = value - 2 * offset_us;\n        if value < 0 - 62135596800000000 || value"),
    ("the sign of the offset is flipped before bucketing", "dtparse.cho", "        if int_of(data[k]) == '-' {\n            offset_us = 0 - offset_us;", "        if int_of(data[k]) == '+' {\n            offset_us = 0 - offset_us;"),
    ("the minutes of the offset are dropped before bucketing", "dtparse.cho", "                offset_us = (oh * 3600 + om * 60) * 1000000;", "                offset_us = (oh * 3600) * 1000000;"),
    # ---- the unit (dtbucket.cho and flags.cho)
    ("an upper case DAY is a unit", "dtbucket.cho", "    if bytes.equal(word, \"day\") {", "    if bytes.equal(word, \"day\") || bytes.equal(word, \"DAY\") {"),
    ("a minute is an hour", "dtbucket.cho", "    if bytes.equal(word, \"hour\") {", "    if bytes.equal(word, \"hour\") || bytes.equal(word, \"minute\") {"),
    ("a week is a unit", "dtbucket.cho", "    if bytes.equal(word, \"week\") {\n        return 1;\n    }\n    let unit = unit_code(word);", "    let unit = unit_code(word);"),
    ("a week is a day", "dtbucket.cho", "    if bytes.equal(word, \"day\") {", "    if bytes.equal(word, \"day\") || bytes.equal(word, \"week\") {"),
    ("a unit finer than the type is accepted", "dtbucket.cho", "    if kind == 22 && unit <= 2 {\n        return 2;", "    if kind == 22 && unit <= 0 {\n        return 2;"),
    ("a day of a date is accepted", "dtbucket.cho", "    if kind == 22 && unit <= 2 {\n        return 2;", "    if kind == 22 && unit <= 1 {\n        return 2;"),
    ("a month of a date is refused as finer", "dtbucket.cho", "    if kind == 22 && unit <= 2 {\n        return 2;", "    if kind == 22 && unit <= 3 {\n        return 2;"),
    ("an hour of a timestamp is refused as finer", "dtbucket.cho", "    if kind == 22 && unit <= 2 {\n        return 2;", "    if kind >= 22 && unit <= 1 {\n        return 2;"),
    ("an unknown word is a week", "dtbucket.cho", "    if unit == 0 {\n        return 3;\n    }", "    if unit == 0 {\n        return 1;\n    }"),
    ("an escaped colon is read as a type before @", "dtbucket.cho", "                if dk != 0 && !escaped_at(item, i - dl) {\n                    return (i, dl, dk);", "                if dk != 0 {\n                    return (i, dl, dk);"),
    ("an @ without a type before it begins a unit", "dtbucket.cho", "                if dk != 0 && !escaped_at(item, i - dl) {\n                    return (i, dl, dk);", "                if !escaped_at(item, i - dl) {\n                    return (i, dl, dk);"),
    ("the refusal names the unit as the item", "dtbucket.cho", "    w = fail.detail_text(heap, w, \"item\", item);", "    w = fail.detail_text(heap, w, \"item\", unit);"),
    ("the reason of a week is unknown-unit", "dtbucket.cho", "    if class == 1 {\n        return \"week-not-built\";", "    if class == 1 {\n        return \"unknown-unit\";"),
    ("the reason of a finer unit is week-not-built", "dtbucket.cho", "    if class == 2 {\n        return \"finer-than-type\";", "    if class == 2 {\n        return \"week-not-built\";"),
    ("the hint of a week does not say it is not built", "dtbucket.cho", "\"@week is not built: group by", "\"@week is known: group by"),
    ("the hint of a finer unit does not name the type", "dtbucket.cho", "a bucket finer than the type: :date has no hour or day, its units", "a bucket finer than the type: its units"),
    ("the unit is dropped from the key", "flags.cho", "                kind = bdk + 256 * dtbucket.unit_code(item[bat + 1..len(item)]);", "                kind = bdk;"),
    ("the type suffix stays in the name", "flags.cho", "            e = bat - bdl;\n", "            e = bat;\n"),
    ("a refused unit is not reported", "flags.cho", "            } else if bu_class == 0 {\n                bu_class = class;", "            } else if bu_class == 99 {\n                bu_class = class;"),
    ("the bucket is read in --order-by too", "flags.cho", "        if unescape {\n            let (a2, l2, k2) = dtbucket.find_at(item);", "        if true {\n            let (a2, l2, k2) = dtbucket.find_at(item);"),
    ("the unit's refusal is not added to the errors", "flags.cho", "        e = dtbucket.refusal(heap, e, flag, given[bu_start..bu_end], given[bu_start + at + 1..bu_end], bu_class);", "        e = e;"),
    # ---- the plan: the key's type and the conflict check
    ("the base type of a key carries its unit", "query.cho", "    return gkind_raw_at(q, j) % 256;", "    return gkind_raw_at(q, j);"),
    ("the conflict check compares the unit too", "query.cho", "                return (gkind_at(q, k), name);", "                return (gkind_raw_at(q, k), name);"),
    ("the unit of a key is its type", "query.cho", "    return gkind_raw_at(q, j) / 256;", "    return gkind_raw_at(q, j) % 256;"),
    ("a key without a unit is read with the unit's code", "agg.cho", "            let gk = query.gkind_raw_at(tree, j);", "            let gk = query.gkind_at(tree, j);"),
    ("a unit is read as a type that has none", "agg.cho", "        if kind >= 256 {\n            return dtbucket.read(data, kind);\n        }\n", "        if kind >= 512 {\n            return dtbucket.read(data, kind);\n        }\n"),
    ("a key with a unit is read without it", "agg.cho", "            return dtbucket.read(data, kind);", "            return dtparse.parse(data, kind % 256);"),
    # ---- --agg
    ("the scale of a mean is a unit", "dtbucket.cho", "    if n < 1 || n > 2 {\n        return false;\n    }", "    if n < 1 || n > 0 {\n        return false;\n    }"),
    ("a bucket in --agg is accepted (read as a column name)", "dtbucket.cho", "        if agg_bucket(given[start..j]) {\n            return index;", "        if false {\n            return index;"),
    ("a bucket in --agg is found only when the list was not read", "dtfail.cho", "    if parsed == 0 - 1 {\n        return dtbucket.agg_item(given);", "    if parsed == 0 - 3 {\n        return dtbucket.agg_item(given);"),
    ("the hint of a bucket in --agg is the usual one", "dtfail.cho", "    if dtbucket.agg_bucket(item) {\n        return dtbucket.agg_hint();", "    if false {\n        return dtbucket.agg_hint();"),
    ("the hint of a bucket in --agg does not name the flag", "dtbucket.cho", "a bucket belongs to a group key, not to an aggregate: --group ts:timestamp@day --agg", "a bucket belongs to a group key, not to an aggregate: --agg"),
    # ---- the repair (dtfail.cho)
    ("a repair loses the unit", "dtfail.cho", "                out = buffer.append(heap, out, text[start + bat..j]);\n", ""),
    ("a repair to a date keeps an hour", "dtfail.cho", "            if to == 22 && unit <= 2 {", "            if to == 22 && unit <= 0 {"),
    ("a repair to a date keeps a month out", "dtfail.cho", "            if to == 22 && unit <= 2 {", "            if to == 22 && unit <= 3 {"),
    ("a repair with a blocked item is offered", "dtfail.cho", "    if g_n + a_n + o_n == 0 || g_blocked + a_blocked + o_blocked > 0 {", "    if g_n + a_n + o_n == 0 {"),
    ("a repair rewrites an item with a unit of another type", "dtfail.cho", "        if bat >= 0 && bdk == from {", "        if bat >= 0 {"),
    # ---- --explain
    ("explain does not print the bucket", "explain.cho", "        if query.gunit_at(tree, k) != 0 {", "        if false {"),
    ("explain prints the bucket of every key", "explain.cho", "        if query.gunit_at(tree, k) != 0 {", "        if query.gkind_at(tree, k) >= 22 {"),
    ("explain prints the hour for every unit", "explain.cho", "dtbucket.unit_text(query.gunit_at(tree, k))", "dtbucket.unit_text(1)"),
    ("a month is written year", "dtbucket.cho", "    if unit == 3 {\n        return \"month\";", "    if unit == 3 {\n        return \"year\";"),
    # ---- the help and the hint of the query
    ("the help does not say the period is the UTC one", "spec.cho", "is the UTC one of its instant", "is the one of its instant"),
    ("the hint of a typed group by in a query does not name the flag", "qtext.cho", "a period of a date, a timestamp or a datetime is --group ts:timestamp@day (or @hour, @month, @year)", "a period of a date, a timestamp or a datetime is a flag"),
]

if __name__ == "__main__":
    shard = os.environ.get("MUT_SHARD")
    chosen = MUTANTS
    if shard:
        i, n = (int(x) for x in shard.split("/"))
        chosen = [m for k, m in enumerate(MUTANTS) if k % n == i]
    sys.exit(mutlib.main(chosen, TESTS))
