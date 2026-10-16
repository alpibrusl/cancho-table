#!/usr/bin/env python3
"""Mutation check of `:date`, `:timestamp` and `:datetime` (docs/datetime.md, gate G-MUT): each mutant is one source file of tools/table with one deliberate defect, rebuilt, and run
against the date tests (tests/conformance/test_datetime.py, with the reference datetime_ref.py) and the rules. A mutant is killed when a test fails or the build refuses it. The files
are restored after every mutant.

    python3 scripts/datetime_mutants.py [name-substring ...]

`--check` only verifies that every site still matches exactly once (no build; CI runs it). See mutlib.py.

The mutants are grouped as the doc's G-MUT lists them: the calendar (the leap rule, the month lengths, the day number and its inverse), the clock (24 and 60), the offset (sign, minutes,
applied twice or not at all, `-00:00`), the fraction (cut, rounded, 7 digits), the range (the year ends, checked before the offset), the order of the checks, the printer (the 0/3/6 digit
rule, the Z), the type suffixes and the codes (a sum accepted, an escaped colon read as a suffix), the refusals (abort codes, the stride of the fast path, the direction, the repairs), the
literals and the operators, the output (JSON strings) and the plan (explain, the type name). No mutant is equivalent unless a comment says so.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_datetime", "test_rules"]

# the operators of `holds_date`, the one place the three types compare a cell with its literals (expr.cho)
HOLDS_HEAD = "    let (v, bad) = dtparse.parse(cell, typed);\n    if bad != 0 {\n        return bad;\n    }\n    var l = 0;\n    while l < many {\n        let w = query.lit_at(plan, first + l, 2);\n        var yes = false;\n        if kind == 2 || op == 0 {\n            yes = v == w;\n"
HOLDS_CHAIN = ("        } else if op == 1 {\n            yes = v != w;\n        } else if op == 2 {\n            yes = v < w;\n        } else if op == 3 {\n            yes = v <= w;\n"
               "        } else if op == 4 {\n            yes = v > w;\n        } else {\n            yes = v >= w;\n        }\n        if yes {\n            return 1;\n        }\n        l = l + 1;\n    }\n    return 0;\n}\n\n// Whether `cell` satisfies condition")


# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # ---- the calendar (dtparse.cho)
    ("the leap rule forgets the centuries", "dtparse.cho", "y % 4 == 0 && (y % 100 != 0 || y % 400 == 0)", "y % 4 == 0"),
    ("the leap rule forgets the fourth centuries", "dtparse.cho", "y % 4 == 0 && (y % 100 != 0 || y % 400 == 0)", "y % 4 == 0 && y % 100 != 0"),
    ("the leap rule is every fifth year", "dtparse.cho", "y % 4 == 0 && (y % 100 != 0 || y % 400 == 0)", "y % 5 == 0 && (y % 100 != 0 || y % 400 == 0)"),
    ("February has 29 days every year", "dtparse.cho", "        return 28;\n    }\n    if m == 4", "        return 29;\n    }\n    if m == 4"),
    ("February has 28 days every year", "dtparse.cho", "    if m == 2 {\n        if is_leap(y) {\n            return 29;", "    if m == 2 {\n        if false {\n            return 29;"),
    ("April has 31 days", "dtparse.cho", "if m == 4 || m == 6 || m == 9 || m == 11 {", "if m == 6 || m == 9 || m == 11 {"),
    ("June has 31 days", "dtparse.cho", "if m == 4 || m == 6 || m == 9 || m == 11 {", "if m == 4 || m == 9 || m == 11 {"),
    ("September has 31 days", "dtparse.cho", "if m == 4 || m == 6 || m == 9 || m == 11 {", "if m == 4 || m == 6 || m == 11 {"),
    ("November has 31 days", "dtparse.cho", "if m == 4 || m == 6 || m == 9 || m == 11 {", "if m == 4 || m == 6 || m == 9 {"),
    ("a month 13 is read", "dtparse.cho", "    if month < 1 || month > 12 {\n        return refuse(8, 5);", "    if month < 1 || month > 13 {\n        return refuse(8, 5);"),
    ("a month 0 is read", "dtparse.cho", "    if month < 1 || month > 12 {\n        return refuse(8, 5);", "    if month < 0 || month > 12 {\n        return refuse(8, 5);"),
    ("a day 0 is read", "dtparse.cho", "    if day < 1 || day > days_in_month(year, month) {\n        return refuse(9, 8);", "    if day < 0 || day > days_in_month(year, month) {\n        return refuse(9, 8);"),
    ("the last day of a month is refused", "dtparse.cho", "    if day < 1 || day > days_in_month(year, month) {\n        return refuse(9, 8);", "    if day < 1 || day >= days_in_month(year, month) {\n        return refuse(9, 8);"),
    ("the day of a date is checked against 31", "dtparse.cho", "    if d < 1 || d > days_in_month(y, m) {", "    if d < 1 || d > 31 {"),
    ("the month of a date 13 is read", "dtparse.cho", "    if m < 1 || m > 12 {\n        return refuse(3, 5);", "    if m < 1 || m > 13 {\n        return refuse(3, 5);"),
    ("the year 0 of a date is read, the year 1 refused", "dtparse.cho", "    if y == 0 {\n        return refuse(2, 0);", "    if y == 1 {\n        return refuse(2, 0);"),
    ("the year 0 of a timestamp is read", "dtparse.cho", "    if year == 0 {\n        return refuse(7, 0);", "    if year == 10000 {\n        return refuse(7, 0);"),
    ("the year 9999 of a timestamp is refused", "dtparse.cho", "    if year == 0 {\n        return refuse(7, 0);", "    if year == 0 || year == 9999 {\n        return refuse(7, 0);"),
    # ---- the day number and its inverse
    ("the era is 399 years", "dtparse.cho", "    let era = yy / 400;", "    let era = yy / 399;"),
    ("the era has one day too few", "dtparse.cho", "    return era * 146097 + doe - 719468;", "    return era * 146096 + doe - 719468;"),
    ("the day of the year is off by a day", "dtparse.cho", "    let doy = (153 * mp + 2) / 5 + d - 1;", "    let doy = (153 * mp + 1) / 5 + d - 1;"),
    ("the century leap day is not taken off", "dtparse.cho", "    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;", "    let doe = yoe * 365 + yoe / 4 + doy;"),
    ("January and February belong to the year they are in", "dtparse.cho", "        yy = y - 1;", "        yy = y;"),
    ("the epoch is a day late", "dtparse.cho", "    return era * 146097 + doe - 719468;", "    return era * 146097 + doe - 719467;"),
    ("the inverse's era is one day short", "dtparse.cho", "(doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;", "(doe - doe / 1460 + doe / 36524 - doe / 146097) / 365;"),
    ("the inverse forgets the leap day of the fourth year", "dtparse.cho", "(doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;", "(doe + doe / 36524 - doe / 146096) / 365;"),
    ("the inverse's January belongs to the year before", "dtparse.cho", "    if mp >= 10 {\n        m = mp - 9;\n        y = y + 1;", "    if mp >= 11 {\n        m = mp - 9;\n        y = y + 1;"),
    ("the inverse's January and February keep the year", "dtparse.cho", "        m = mp - 9;\n        y = y + 1;", "        m = mp - 9;"),
    ("the inverse's day of the month is one high", "dtparse.cho", "    let d = doy - (153 * mp + 2) / 5 + 1;", "    let d = doy - (153 * mp + 2) / 5 + 2;"),
    # ---- the clock
    ("hour 24 is read as a time", "dtparse.cho", "    if hh == 24 {\n        return refuse(11, 11);\n    }\n    if hh > 23 {", "    if hh > 24 {"),
    ("hour 24 is refused as any other hour", "dtparse.cho", "    if hh == 24 {\n        return refuse(11, 11);", "    if hh == 124 {\n        return refuse(11, 11);"),
    ("hour 23 is refused", "dtparse.cho", "    if hh > 23 {\n        return refuse(10, 11);", "    if hh > 22 {\n        return refuse(10, 11);"),
    ("minute 60 is read", "dtparse.cho", "    if mi > 59 {", "    if mi > 60 {"),
    ("a leap second is read as a second", "dtparse.cho", "    if ss == 60 {\n        return refuse(14, 17);\n    }\n    if ss > 60 {", "    if ss > 60 {"),
    ("second 61 is read", "dtparse.cho", "    if ss > 60 {", "    if ss > 61 {"),
    # ---- the offset
    ("the sign of the offset is flipped", "dtparse.cho", "        if int_of(data[k]) == '-' {\n            offset_us = 0 - offset_us;", "        if int_of(data[k]) == '+' {\n            offset_us = 0 - offset_us;"),
    ("the offset is applied twice", "dtparse.cho", "        value = value - offset_us;", "        value = value - 2 * offset_us;"),
    ("the offset is not applied", "dtparse.cho", "        value = value - offset_us;", "        value = value;"),
    ("the minutes of the offset are dropped", "dtparse.cho", "        offset_us = (oh * 3600 + om * 60) * 1000000;", "        offset_us = (oh * 3600) * 1000000;"),
    ("the hours of the offset are dropped", "dtparse.cho", "        offset_us = (oh * 3600 + om * 60) * 1000000;", "        offset_us = (om * 60) * 1000000;"),
    ("an offset of 24 hours is read", "dtparse.cho", "        if oh > 23 || om > 59 {", "        if oh > 24 || om > 59 {"),
    ("an offset of 60 minutes is read", "dtparse.cho", "        if oh > 23 || om > 59 {", "        if oh > 23 || om > 60 {"),
    ("-00:00 is UTC", "dtparse.cho", "        if tail == 2 && offset_us == 0 && int_of(data[k]) == '-' {", "        if false && tail == 2 && offset_us == 0 && int_of(data[k]) == '-' {"),
    ("an offset of a minute is -00:00", "dtparse.cho", "        if tail == 2 && offset_us == 0 && int_of(data[k]) == '-' {", "        if tail == 2 && offset_us <= 60000000 && int_of(data[k]) == '-' {"),
    ("a timestamp with no offset is read", "dtparse.cho", "        if tail == 0 {\n            return refuse(20, 0);", "        if false {\n            return refuse(20, 0);"),
    ("a datetime with an offset is read", "dtparse.cho", "    } else if tail != 0 {\n        return refuse(22, 0);", "    } else if tail != 0 && tail != 2 {\n        return refuse(22, 0);"),
    ("a datetime with a Z is read", "dtparse.cho", "    } else if tail != 0 {\n        return refuse(22, 0);", "    } else if tail != 0 && tail != 1 {\n        return refuse(22, 0);"),
    ("+hhmm is read as an offset", "dtparse.cho", "    return len(tail) == 6 && (int_of(tail[0]) == '+'", "    return len(tail) >= 5 && (int_of(tail[0]) == '+'"),
    # ---- the fraction
    ("a fraction is cut at the millisecond", "dtparse.cho", "    while i < frac {\n        micro = micro * 10 + int_of(data[20 + i]) - '0';", "    while i < frac && i < 3 {\n        micro = micro * 10 + int_of(data[20 + i]) - '0';"),
    ("a fraction is not scaled to microseconds", "dtparse.cho", "    while i < 6 && frac > 0 {\n        micro = micro * 10;", "    while i < 5 && frac > 0 {\n        micro = micro * 10;"),
    ("seven fractional digits are read", "dtparse.cho", "    if frac > 6 {", "    if frac > 7 {"),
    ("six fractional digits are refused", "dtparse.cho", "    if frac > 6 {", "    if frac > 5 {"),
    ("the digits of a refused fraction are one short", "dtparse.cho", "        return refuse(17, frac);", "        return refuse(17, frac - 1);"),
    # ---- the range
    ("the range is checked before the offset is applied",
     "dtparse.cho",
     "        value = value - offset_us;\n        if value < 0 - 62135596800000000 {\n            return refuse(18, 0);\n        }\n        if value > 253402300799999999 {\n            return refuse(19, 0);\n        }",
     "        if value < 0 - 62135596800000000 {\n            return refuse(18, 0);\n        }\n        if value > 253402300799999999 {\n            return refuse(19, 0);\n        }\n        value = value - offset_us;"),
    ("the first instant is refused", "dtparse.cho", "        if value < 0 - 62135596800000000 {", "        if value <= 0 - 62135596800000000 {"),
    ("the last instant is refused", "dtparse.cho", "        if value > 253402300799999999 {", "        if value >= 253402300799999999 {"),
    ("an instant before the first one is after", "dtparse.cho", "            return refuse(18, 0);", "            return refuse(19, 0);"),
    ("an instant after the last one is read", "dtparse.cho", "        if value > 253402300799999999 {", "        if value > 253402300799999999 + 86400000000 {"),
    # ---- the order of the checks
    ("the fraction is checked after the offset's presence",
     "dtparse.cho",
     "    if frac > 6 {\n        return refuse(17, frac);\n    }\n    if kind == 23 {\n        if tail == 0 {\n            return refuse(20, 0);\n        }",
     "    if kind == 23 && tail == 0 {\n        return refuse(20, 0);\n    }\n    if frac > 6 {\n        return refuse(17, frac);\n    }\n    if kind == 23 {\n        if false {\n            return refuse(20, 0);\n        }"),
    ("the clock is checked before the calendar",
     "dtparse.cho",
     "    let year = four(data, 0);\n    let month = two(data, 5);\n    let day = two(data, 8);\n    if year == 0 {",
     "    if two(data, 11) == 24 {\n        return refuse(11, 11);\n    }\n    let year = four(data, 0);\n    let month = two(data, 5);\n    let day = two(data, 8);\n    if year == 0 {"),
    # ---- the shape
    ("a date alone is a midnight", "dtparse.cho", "    if n == 10 {\n        return refuse(16, 10);", "    if n == 1000 {\n        return refuse(16, 10);"),
    ("a lower case t is refused", "dtparse.cho", "    if sep != 'T' && sep != 't' && sep != ' ' {", "    if sep != 'T' && sep != ' ' {"),
    ("a space between the date and the time is refused", "dtparse.cho", "    if sep != 'T' && sep != 't' && sep != ' ' {", "    if sep != 'T' && sep != 't' {"),
    ("a lower case z is refused", "dtparse.cho", "        if n - k == 1 && (int_of(data[k]) == 'Z' || int_of(data[k]) == 'z') {\n            tail = 1;", "        if n - k == 1 && int_of(data[k]) == 'Z' {\n            tail = 1;"),
    ("a time in a date is truncated", "dtparse.cho", "    if n > 10 {\n        // a time part", "    if n > 1000 {\n        // a time part"),
    ("a time in a date is a fault of shape", "dtparse.cho", "        if s1 == 0 {\n            return refuse(5, 10);", "        if s1 == 0 && false {\n            return refuse(5, 10);"),
    ("a named zone is a fault of shape", "dtparse.cho", "        if j == len(tail) {\n            return refuse(23, 0);", "        if j == len(tail) {\n            return refuse(6, k);"),
    ("a bracketed suffix is a fault of shape", "dtparse.cho", "    if c == '[' {\n        return refuse(23, 0);", "    if c == 200 {\n        return refuse(23, 0);"),
    ("the offset of a fault of shape is one high", "dtparse.cho", "    return refuse(6, k);\n}", "    return refuse(6, k + 1);\n}"),
    # ---- the printer
    ("a fraction is always six digits", "dtparse.cho", "        if micro % 1000 == 0 {", "        if false {"),
    ("a fraction of a hundred microseconds is three digits", "dtparse.cho", "        if micro % 1000 == 0 {", "        if micro % 100 == 0 {"),
    ("a whole second has a fraction", "dtparse.cho", "    if micro != 0 {\n        o = buffer.push(heap, o, byte_of('.'));", "    if true {\n        o = buffer.push(heap, o, byte_of('.'));"),
    ("a timestamp is written without its Z", "dtparse.cho", "    if kind == 23 {\n        o = buffer.push(heap, o, byte_of('Z'));", "    if kind == 123 {\n        o = buffer.push(heap, o, byte_of('Z'));"),
    ("a datetime is written with a Z", "dtparse.cho", "    if kind == 23 {\n        o = buffer.push(heap, o, byte_of('Z'));", "    if kind >= 23 {\n        o = buffer.push(heap, o, byte_of('Z'));"),
    ("the day of a printed date is one high", "dtparse.cho", "    return put2(heap, o, d);", "    return put2(heap, o, d + 1);"),
    ("a printed instant is a day early", "dtparse.cho", "    var o = put_date(heap, out, days + 306);", "    var o = put_date(heap, out, days + 305);"),
    ("the minutes of a printed instant are the seconds", "dtparse.cho", "    o = put2(heap, o, rest / 60 % 60);", "    o = put2(heap, o, rest % 60);"),
    ("a date is written at the epoch of a timestamp", "dtparse.cho", "        return put_date(heap, out, v + 719468);", "        return put_date(heap, out, v + 719469);"),
    # ---- the suffixes and the type codes
    ("an escaped colon ends a name in a suffix", "dtparse.cho", "bytes.equal(word[n - 5..n], \":date\") && escaped <= n - 5", "bytes.equal(word[n - 5..n], \":date\")"),
    ("a column called :date is a suffix with no name", "dtparse.cho", "    if n > 5 && bytes.equal(word[n - 5..n], \":date\")", "    if n >= 5 && bytes.equal(word[n - 5..n], \":date\")"),
    ("a timestamp suffix is a datetime", "dtparse.cho", "    if n > 10 && bytes.equal(word[n - 10..n], \":timestamp\") && escaped <= n - 10 {\n        return (10, 23);", "    if n > 10 && bytes.equal(word[n - 10..n], \":timestamp\") && escaped <= n - 10 {\n        return (10, 24);"),
    ("a date suffix after a quoted name is not read", "dtparse.cho", "    if at + 5 <= len(src) && bytes.equal(src[at..at + 5], \":date\") {", "    if at + 5 <= len(src) && bytes.equal(src[at..at + 5], \":dat3\") {"),
    ("a date is read by the reader of decimals", "query.cho", "    if kind >= 22 {\n        // :date, :timestamp, :datetime", "    if kind >= 25 {\n        // :date, :timestamp, :datetime"),
    ("a date is read as a datetime by an aggregate", "agg.cho", "    if dk != 0 && !escaped_at(item, e - dl) {\n        return (dl, dk);", "    if dk != 0 && !escaped_at(item, e - dl) {\n        return (dl, 24);"),
    ("a sum of dates is read", "agg.cho", "                } else if function == 1 && kind >= 22 {", "                } else if function == 1 && kind >= 25 {"),
    ("an escaped colon ends an aggregate's column in a suffix", "agg.cho", "    if dk != 0 && !escaped_at(item, e - dl) {", "    if dk != 0 {"),
    ("a group key's suffix stays in its name", "agg.cho", "    if dk != 0 {\n        return (e - dl, dk);", "    if dk != 0 {\n        return (e, dk);"),
    ("a date key is written as a decimal", "agg.cho", "    if kind >= 22 {\n        return dtparse.put(heap, out, v, kind);", "    if kind >= 25 {\n        return dtparse.put(heap, out, v, kind);"),
    ("a date minimum is written as a decimal", "agg.cho", "query.agg_at(tree, k, 2) >= 22 {\n        return dtparse.put", "query.agg_at(tree, k, 2) >= 25 {\n        return dtparse.put"),
    # ---- the literals and the operators (expr.cho)
    ("a cell that is not a date is false", "expr.cho", "    let (v, bad) = dtparse.parse(cell, typed);\n    if bad != 0 {\n        return bad;\n    }", "    let (v, bad) = dtparse.parse(cell, typed);\n    if bad != 0 {\n        return 0;\n    }"),
    ("equal instants are two", "expr.cho", HOLDS_HEAD + HOLDS_CHAIN, HOLDS_HEAD.replace("yes = v == w;", "yes = v == w + 1;") + HOLDS_CHAIN),
    ("a date's != is =", "expr.cho", HOLDS_HEAD + HOLDS_CHAIN, HOLDS_HEAD + HOLDS_CHAIN.replace("yes = v != w;", "yes = v == w;", 1)),
    ("a date's < is <=", "expr.cho", HOLDS_HEAD + HOLDS_CHAIN, HOLDS_HEAD + HOLDS_CHAIN.replace("yes = v < w;", "yes = v <= w;", 1)),
    ("a date's <= is <", "expr.cho", HOLDS_HEAD + HOLDS_CHAIN, HOLDS_HEAD + HOLDS_CHAIN.replace("yes = v <= w;", "yes = v < w;", 1)),
    ("a date's > is >=", "expr.cho", HOLDS_HEAD + HOLDS_CHAIN, HOLDS_HEAD + HOLDS_CHAIN.replace("yes = v > w;", "yes = v >= w;", 1)),
    ("a date's >= is >", "expr.cho", HOLDS_HEAD + HOLDS_CHAIN, HOLDS_HEAD + HOLDS_CHAIN.replace("yes = v >= w;", "yes = v > w;", 1)),
    ("a date's in is false", "expr.cho", HOLDS_HEAD + HOLDS_CHAIN, HOLDS_HEAD.replace("if kind == 2 || op == 0 {", "if op == 0 {") + HOLDS_CHAIN),
    ("a literal's refusal expects the wrong type", "expr.cho", "                    what = 3 + typed;", "                    what = 2 + typed;"),
    ("contains on a date is a comparison of numbers", "expr.cho", "    if typed >= 22 {\n        return 28;", "    if typed >= 25 {\n        return 28;"),
    ("a date suffix is not read after a quoted name", "expr.cho", "                if dk != 0 && (after + dl == len(src) || is_delimiter(int_of(src[after + dl]))) {", "                if dk != 0 && (after + dl == len(src) && false) {"),
    ("a date suffix is read after an escaped colon", "expr.cho", "dtparse.suffix_in(buffer.bytes(wr)[0..length], escaped_last);", "dtparse.suffix_in(buffer.bytes(wr)[0..length], 0);"),
    ("a date condition is read as a decimal condition", "expr.cho", "        if typed >= 22 {\n            return holds_date(", "        if typed >= 25 {\n            return holds_date("),
    # ---- the refusals
    ("a date refusal of --where has the abort of a float", "engine.cho", "        code = 1000 + verdict;", "        code = 1001 + verdict;"),
    ("a date refusal of an aggregate has another abort", "engine.cho", "        return 1000 + status;", "        return 1064 + status;"),
    ("a date refusal of a sort key is read as a decimal's", "engine.cho", "    if status >= 5 {\n        // a typed key's cell", "    if status >= 15 {\n        // a typed key's cell"),
    ("the fast path's refusals leave no room for a date's status", "engine.cho", "    return 1099511627776;", "    return 16;"),
    ("the abort of a date is read by the rule of the decimals", "finish.cho", "    } else if c.abort >= 1000 {", "    } else if c.abort >= 100000 {"),
    ("a type is named by its neighbour", "finish.cho", "    if t == 23 {\n        return \":timestamp\";", "    if t == 23 {\n        return \":datetime\";"),
    ("a sum of dates has the usual hint", "dtfail.cho", "    if dk != 0 && (bytes.starts_with(item, \"sum:\") || bytes.starts_with(item, \"mean:\")) {", "    if dk != 0 && bytes.starts_with(item, \"total:\") {"),
    ("the direction of a range refusal is swapped", "dtfail.cho", "        w = fail.detail_str(heap, w, \"direction\", \"before\");", "        w = fail.detail_str(heap, w, \"direction\", \"after\");"),
    ("the digits of a refusal are not said", "dtfail.cho", "    } else if reason == 17 {\n        w = fail.detail_int(heap, w, \"digits\", extra_n);", "    } else if reason == 117 {\n        w = fail.detail_int(heap, w, \"digits\", extra_n);"),
    ("the offset of a refusal is the reason's number", "dtfail.cho", "        w = fail.detail_int(heap, w, \"offset\", extra_n);", "        w = fail.detail_int(heap, w, \"offset\", reason);"),
    ("a value is cut at 65 bytes", "dtfail.cho", "    w = fail.detail_bool(heap, w, \"value_truncated\", len(kept) >= 64);", "    w = fail.detail_bool(heap, w, \"value_truncated\", len(kept) > 64);"),
    ("a missing offset is repaired as a date", "dtfail.cho", "    if reason == 20 {\n        return 24;", "    if reason == 20 {\n        return 22;"),
    ("an offset present is repaired as a datetime", "dtfail.cho", "    if reason == 22 {\n        return 23;", "    if reason == 22 {\n        return 24;"),
    ("a date alone is repaired as a timestamp", "dtfail.cho", "    if reason == 16 {\n        return 22;", "    if reason == 16 {\n        return 23;"),
    ("a time in a date is repaired as a datetime always", "dtfail.cho", "        if s1 == 0 {\n            return 23;", "        if s1 == 0 {\n            return 24;"),
    ("a repair is offered for a condition's cell", "dtfail.cho", "    let context_ok = err_fn == 0 - 3 || err_fn == 0 - 2 || err_fn >= 2 && err_fn <= 4;", "    let context_ok = true;"),
    ("a repair is offered while a condition has literals of the old type", "dtfail.cho", "        if query.cond_at(tree, k, 1) == from && (bytes.equal(query.name_at(tree, query.cond_at(tree, k, 3)), name) || query.position_of(query.name_at(tree, query.cond_at(tree, k, 3))) >= 0) {\n            return false;", "        if false {\n            return false;"),
    ("a repair is offered while a condition called by position has literals of the old type", "dtfail.cho", "|| query.position_of(query.name_at(tree, query.cond_at(tree, k, 3))) >= 0) {", ") {"),
    ("a repair rewrites the type of another column", "dtfail.cho", "        if query.agg_at(tree, k, 0) != 0 && query.agg_at(tree, k, 2) == from && !bytes.equal(query.name_at(tree, query.agg_at(tree, k, 1)), name) {\n            return false;", "        if false {\n            return false;"),
    ("a repair rewrites a column whose name ends in an escaped colon and a type", "dtfail.cho", "        if dk == from && !escaped_at(text, j - dl) {", "        if dk == from {"),
    # ---- the literals' repair (dtrepair.cho)
    ("the repair of < is the next day", "dtrepair.cho", "    if op == 5 || op == 2 {", "    if op == 5 {"),
    ("the repair of > is the day itself", "dtrepair.cho", "            if op == 4 {\n                out = buffer.append(heap, out, \" >= '\");", "            if op == 44 {\n                out = buffer.append(heap, out, \" >= '\");"),
    ("the repair of = is open on the right", "dtrepair.cho", "            out = both(heap, out, column, days, \">=\", \" and \", \"<\");", "            out = both(heap, out, column, days, \">=\", \" and \", \"<=\");"),
    ("the repair of != is an and", "dtrepair.cho", "            out = both(heap, out, column, days, \"<\", \" or \", \">=\");", "            out = both(heap, out, column, days, \"<\", \" and \", \">=\");"),
    # ---- the output and the plan
    ("a date key is a bare number in JSON lines", "jout.cho", "            if query.gkind_at(tree, m) >= 22 {", "            if query.gkind_at(tree, m) >= 25 {"),
    ("a distinct count of dates is a string in JSON lines", "jout.cho", "(query.agg_at(tree, m - ng, 0) == 2 || query.agg_at(tree, m - ng, 0) == 3)", "(query.agg_at(tree, m - ng, 0) == 2 || query.agg_at(tree, m - ng, 0) == 4)"),
    ("a maximum of dates is a bare number in JSON lines", "jout.cho", "(query.agg_at(tree, m - ng, 0) == 2 || query.agg_at(tree, m - ng, 0) == 3)", "(query.agg_at(tree, m - ng, 0) == 2 || query.agg_at(tree, m - ng, 0) == 33)"),
    ("explain names every date type the same", "explain.cho", "        b = buffer.append(heap, b, dtparse.type_text(t));", "        b = buffer.append(heap, b, dtparse.type_text(22));"),
    ("explain lists the refusals of a date for a timestamp", "dtfail.cho", "    if dts & 1 != 0 {\n        b = rule(heap, b, \"value.not-date\");", "    if dts & 2 != 0 {\n        b = rule(heap, b, \"value.not-date\");"),
    ("explain lists the offset refusal of a datetime for a timestamp", "dtfail.cho", "    if dts & 4 != 0 {\n        b = rule(heap, b, \"value.timestamp-offset-present\");", "    if dts & 2 != 0 {\n        b = rule(heap, b, \"value.timestamp-offset-present\");"),
    ("a date's reason is not said", "dtparse.cho", "    if reason == 5 {\n        return \"time-part\";", "    if reason == 5 {\n        return \"shape\";"),
    ("unknown-offset is said missing", "dtparse.cho", "    if reason == 21 {\n        return \"unknown-offset\";", "    if reason == 21 {\n        return \"missing\";"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
