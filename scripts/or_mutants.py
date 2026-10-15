#!/usr/bin/env python3
"""Mutation check of the repair of an `or` (docs/or-and-columns.md section 9, G-MUT, slice A0): `a = 1 or a = 2` is refused as it always was, and the refusal offers `a in (1, 2)` where
that is the same question (tests/conformance/test_or_a0.py, tests/conformance/or_a0.py).

Each mutant is the repair broken in one way, in the flag form (`expr.or_in`, `table.cho`) or in the query form (`qparse.or_chain`, `qrepair`), rebuilt and run against
test_or_a0. A mutant is killed when a test fails or the build refuses it; one that survives is a hole in the gates, or an equivalent mutant, which is then explained under
EQUIVALENT and left out of MUTANTS.

The mutants that the document names for A0 ("`in` merged across columns", "the A0 repair offered for `!=`") are the first two groups. A1 will add the rest of the document's list
(`or` that is `and`, the exits swapped, `not`, precedence, the depth) to this script.

    python3 scripts/or_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_or_a0"]

MUTANTS = [
    # ---- the flag form: what may be merged
    ("or_in: != is merged into the in", "expr.cho", "if w2 != 0 || kind == 1 || kind == 0 && op != 0 {", "if w2 != 0 || kind == 1 {"),
    ("or_in: contains is merged into the in", "expr.cho", "if w2 != 0 || kind == 1 || kind == 0 && op != 0 {", "if w2 != 0 || kind == 0 && op != 0 {"),
    ("or_in: another column is merged into the in", "expr.cho",
     "} else if after_column - begins != col_b - col_a || !bytes.equal(src[begins..after_column], src[col_a..col_b]) {", "} else if false {"),
    ("or_in: only the length of the column is compared", "expr.cho",
     "} else if after_column - begins != col_b - col_a || !bytes.equal(src[begins..after_column], src[col_a..col_b]) {", "} else if after_column - begins != col_b - col_a {"),
    ("or_in: only the first byte of the column is compared (the type suffix is not)", "expr.cho",
     "!bytes.equal(src[begins..after_column], src[col_a..col_b]) {", "!bytes.equal(src[begins..begins + 1], src[col_a..col_a + 1]) {"),
    ("or_in: a quoted or is a connective", "expr.cho", "is_or = status == 0 && quoted == 0 && bytes.equal(buffer.bytes(wr), \"or\");", "is_or = status == 0 && bytes.equal(buffer.bytes(wr), \"or\");"),
    ("or_in: and is a connective too", "expr.cho", "is_or = status == 0 && quoted == 0 && bytes.equal(buffer.bytes(wr), \"or\");",
     "is_or = status == 0 && quoted == 0 && (bytes.equal(buffer.bytes(wr), \"or\") || bytes.equal(buffer.bytes(wr), \"and\"));"),
    ("or_in: the corrected expression is not parsed again (the ceiling of an in is not kept)", "expr.cho", "if what == 0 {\n            buffer.drop(heap, list);\n            return (out, true);", "if what == 0 || what != 0 {\n            buffer.drop(heap, list);\n            return (out, true);"),
    ("or_in: the values are joined without a space (the list is another list)", "expr.cho", "list = buffer.append(heap, list, \", \");", "list = buffer.append(heap, list, \",\");"),
    ("or_in: the second leaf's values are lost", "expr.cho", "list = buffer.append(heap, list, src[i..after_value]);", "if leaves == 0 {\n                            list = buffer.append(heap, list, src[i..after_value]);\n                        }"),
    ("or_in: the in is not closed", "expr.cho", "out = buffer.push(heap, out, byte_of(41));", "out = buffer.push(heap, out, byte_of(32));"),
    ("or_in: the column is cut short", "expr.cho", "out = buffer.append(heap, out, src[col_a..col_b]);", "out = buffer.append(heap, out, src[col_a..col_b - 1]);"),
    ("or_in: an in list is read as one value", "expr.cho", "var listing = kind == 2;", "var listing = false;"),
    # ---- the flag form: where it is offered
    ("table: the repair is offered on the wrong argument", "table.cho", "o = fail.choose_option_replacing(heap, o, args, where_at, buffer.bytes(fr));", "o = fail.choose_option_replacing(heap, o, args, where_at + 1, buffer.bytes(fr));"),
    ("table: --where= loses its flag in the repair", "table.cho", "var joined = buffer.append(heap, buffer.empty(heap, 64), \"--where=\");", "var joined = buffer.append(heap, buffer.empty(heap, 64), \"\");"),
    ("table: the form of the flag is mistaken", "table.cho", "if inp.where_whole {", "if !inp.where_whole {"),
    ("table: the repair is never offered", "table.cho", "if what == 4 && where_at >= 0 {", "if what == 40 && where_at >= 0 {"),
    ("rules: where.syntax is declared never repairable", "finish.cho", "where.syntax|2|sometimes|", "where.syntax|2|never|"),
    # ---- the query form
    ("or_chain: another column is merged into the in", "qparse.cho", "} else if b - a != col_b - col_a || !bytes.equal(q[a..b], q[col_a..col_b]) {", "} else if false {"),
    ("or_chain: only the length of the column is compared", "qparse.cho", "} else if b - a != col_b - col_a || !bytes.equal(q[a..b], q[col_a..col_b]) {", "} else if b - a != col_b - col_a {"),
    ("or_chain: != is merged into the in", "qparse.cho", "let op = comparison(m);\n        if op == 0 {\n            step(m);\n            let v = literal(q, m);\n            if v < 0 {\n                return none;\n            }\n            m[base + values] = v;",
     "let op = comparison(m);\n        if op >= 0 {\n            step(m);\n            let v = literal(q, m);\n            if v < 0 {\n                return none;\n            }\n            m[base + values] = v;"),
    ("or_chain: an and after the chain is ignored", "qparse.cho", "if leaves < 2 || is_word(q, m, 0, \"and\") {", "if leaves < 2 {"),
    ("or_chain: an in list takes one value", "qparse.cho", "listing = eat_sym(m, qlex.y_comma());\n            }\n            if !eat_sym(m, qlex.y_close()) {\n                return none;", "listing = false;\n            }\n            if !eat_sym(m, qlex.y_close()) {\n                return none;"),
    ("or_chain: the chain ends at the first or", "qparse.cho", "if is_word(q, m, 0, \"or\") {\n            step(m);\n        } else {\n            more = false;\n        }", "more = false;"),
    ("or_chain: the values are written over the conditions' own", "qparse.cho", "let base = m[qarena.s_rec_base()];\n    var values = 0;\n    var leaves = 0;", "let base = m[qarena.s_conds()];\n    var values = 0;\n    var leaves = 0;"),
    ("qrepair: an or is given no repair", "qrepair.cho", "if kind == 2 && code == 71 {\n        buffer.drop(heap, none);\n        return or_to_in(heap, q, m);", "if kind == 2 && code == 7100 {\n        buffer.drop(heap, none);\n        return or_to_in(heap, q, m);"),
    ("qrepair: the tail of the query is dropped", "qrepair.cho", "out = buffer.append(heap, out, q[chain_end..len(q)]);", "out = buffer.append(heap, out, q[len(q)..len(q)]);"),
    ("qrepair: the head of the query is dropped", "qrepair.cho", "var out = buffer.append(heap, buffer.empty(heap, len(q) + 16), q[0..begins]);", "var out = buffer.append(heap, buffer.empty(heap, len(q) + 16), q[0..0]);"),
    ("qrepair: the values are joined without a separator", "qrepair.cho", "if k > 0 {\n            out = buffer.append(heap, out, \", \");", "if k > 100000 {\n            out = buffer.append(heap, out, \", \");"),
    ("qrepair: the last value is lost", "qrepair.cho", "while k < values {\n        if k > 0 {", "while k < values - 1 {\n        if k > 0 {"),
    ("qrepair: the literals are not checked against --where", "qrepair.cho", "literals_fit = w5 == 0;", "literals_fit = true;"),
]

# Equivalent mutants, left out of MUTANTS with the reason (each was tried, built and survived the suite, and the reason is why it must):
EQUIVALENT = [
    ("or_in: a literal is read untyped by `parse_value` before the corrected expression is parsed", "expr.cho",
     "let (p3, after_value, w3, a3) = parse_value(heap, plan, src, i, typed);\n                    plan = p3;\n                    if w3 != 0 {\n                        ok = false;\n                    } else {\n                        if values > 0 {",
     "let (p3, after_value, w3, a3) = parse_value(heap, plan, src, i, 0);\n                    plan = p3;\n                    if w3 != 0 {\n                        ok = false;\n                    } else {\n                        if values > 0 {",
     "the corrected expression is parsed again by `parse`, with the column's type, and that is where a literal the type does not take is refused (the tests kill the mutant that skips that parse); the first reading only finds where each value ends, which the type does not change"),
    ("or_chain: one condition is a chain", "qparse.cho", "if leaves < 2 || is_word(q, m, 0, \"and\") {", "if leaves < 1 || is_word(q, m, 0, \"and\") {",
     "`or_chain` is asked only for a query refused at an `or` after a condition, so the clause has two conditions by the time its first `or` is met, and a clause of one condition is never asked"),
]

if __name__ == "__main__":
    # the equivalent mutants are not run, but their sites must still be there (a stale entry is a lie about what was proved)
    stale = mutlib.check([e[:4] for e in EQUIVALENT])
    for p in stale:
        print("!! (equivalent) " + p)
    if stale:
        sys.exit(1)
    sys.exit(mutlib.main(MUTANTS, TESTS))
