#!/usr/bin/env python3
"""Mutation check of the typed group and sort keys (docs/numbers.md, stage N5): each mutant is one source file of tools/table with one deliberate defect, rebuilt, and
run against the typed-key, rule, skill and decimal/float tests. A mutant is killed when a test fails or the build refuses it. The files are restored after every mutant.

    python3 scripts/typed_keys_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_typed_keys", "test_sort", "test_rules", "test_skill"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # the group key (agg.cho)
    ("a typed group column is built as text", "agg.cho", "            if gk != 0 {\n                // a typed group column", "            if gk == 99 {\n                // a typed group column"),
    ("the typed key is 7 bytes long", "agg.cho", "                    key = put_length(heap, key, 8);", "                    key = put_length(heap, key, 7);"),
    ("a typed key is not read: a cell that is not the type is a group", "agg.cho", "                if bad != 0 {\n                    key_bad = bad;\n                    key_at = j;\n                } else {", "                if false {\n                    key_bad = bad;\n                    key_at = j;\n                } else {"),
    ("the refusal names the first group column always", "agg.cho", "                    key_bad = bad;\n                    key_at = j;", "                    key_bad = bad;\n                    key_at = 0;"),
    ("the key stops at the first refusal of a later column only", "agg.cho", "        while j < ng && key_bad == 0 {", "        while j < ng {"),
    ("a refused key still looks its group up", "agg.cho", "    var status = key_bad;\n    var which = 1000 + key_at;", "    var status = 0;\n    var which = 1000 + key_at;"),
    ("a typed key's group is the text of its first cell (the typed cell is read, the text is the key)", "agg.cho", "                    key = put_length(heap, key, 8);\n                    key = put_value_key(heap, key, v);", "                    key = put_length(heap, key, 8);\n                    key = put_value_key(heap, key, 0);"),
    # writing a key back
    ("the high half of a key keeps its sign bit", "agg.cho", "    let signed_hi = hi - (hi >> 31) * 4294967296;", "    let signed_hi = hi;"),
    ("the sign bit of a key is not flipped back", "agg.cho", "    return signed_hi * 4294967296 + lo ^ 0 - 9223372036854775807 - 1;", "    return signed_hi * 4294967296 + lo;"),
    ("the low half of a key is read from the high bytes", "agg.cho", "    let lo = int_of(key[4]) << 24 | int_of(key[5]) << 16 | int_of(key[6]) << 8 | int_of(key[7]);", "    let lo = int_of(key[0]) << 24 | int_of(key[5]) << 16 | int_of(key[6]) << 8 | int_of(key[7]);"),
    ("a decimal key is written at the wrong scale", "agg.cho", "dec.put_scaled(heap, out, v, kind - 2);", "dec.put_scaled(heap, out, v, kind - 1);"),
    ("a float key is written as an integer", "agg.cho", "    if kind == 21 {\n        return flt.put_float(heap, out, v);\n    }\n    return dec.put_scaled", "    if kind == 22 {\n        return flt.put_float(heap, out, v);\n    }\n    return dec.put_scaled"),
    ("an integer key is written as a decimal", "agg.cho", "    if kind == 1 {\n        return append_int(heap, out, v);\n    }\n    if kind == 21 {", "    if kind == 21 {"),
    ("a text key is written through the typed path", "agg.cho", "    if kind == 0 {\n        return buffer.append(heap, out, raw);\n    }\n    let v = value_of_key(raw);", "    let v = value_of_key(raw);"),
    ("a typed key in json is not quoted", "agg.cho", "            o = buffer.push(heap, o, byte_of(34));\n            o = put_key(heap, o, g, tree, x, j);\n            o = buffer.push(heap, o, byte_of(34));", "            o = put_key(heap, o, g, tree, x, j);"),
    # the suffix of a list item
    ("a :float suffix is five bytes", "agg.cho", "    if e > 6 && bytes.equal(item[e - 6..e], \":float\") && !escaped_at(item, e - 6) {\n        return (e - 6, 21);", "    if e > 6 && bytes.equal(item[e - 6..e], \":float\") && !escaped_at(item, e - 6) {\n        return (e - 5, 21);"),
    ("an escaped :float is a suffix", "agg.cho", "    if e > 6 && bytes.equal(item[e - 6..e], \":float\") && !escaped_at(item, e - 6) {", "    if e > 6 && bytes.equal(item[e - 6..e], \":float\") {"),
    ("an escaped :int is a suffix", "agg.cho", "    if e > 4 && bytes.equal(item[e - 4..e], \":int\") && !escaped_at(item, e - 4) {", "    if e > 4 && bytes.equal(item[e - 4..e], \":int\") {"),
    ("a bare :float is a suffix of nothing", "agg.cho", "    if e > 6 && bytes.equal(item[e - 6..e], \":float\")", "    if e >= 6 && bytes.equal(item[e - 6..e], \":float\")"),
    ("a scale of 19 is a scale", "agg.cho", "            if v > 18 {\n                return (e, 99);", "            if v > 19 {\n                return (e, 99);"),
    ("a :dec scale is its digits plus one", "agg.cho", "            return (k - 5, 2 + v);\n        }\n    }\n    return (e, 0);", "            return (k - 5, 3 + v);\n        }\n    }\n    return (e, 0);"),
    ("a :dec of three digits is a scale", "agg.cho", "        if digits >= 1 && digits <= 2 && k >= 6 &&", "        if digits >= 1 && digits <= 3 && k >= 6 &&"),
    # the read of a key cell (engine.cho, query.cho)
    ("a typed group takes the fast path", "agg.cho", "    if contents(g.memo)[rest_at + 2] != 0 {\n        return (0 - 1, 0);", "    if false && contents(g.memo)[rest_at + 2] != 0 {\n        return (0 - 1, 0);"),
    ("the typed flag of a plan is never set", "agg.cho", "contents(mw)[memo_size() + 4] = query.typed_groups(tree);", "contents(mw)[memo_size() + 4] = 0;"),
    ("a text group column counts as typed", "query.cho", "    if kind != 0 {\n        borrow mut g as &!gw in {", "    if kind == 0 {\n        borrow mut g as &!gw in {"),
    ("a group refusal is read as an aggregate's", "engine.cho", "    if k >= 1000 {\n        column = cols[query.count_of(tree, 0)", "    if k >= 100000 {\n        column = cols[query.count_of(tree, 0)"),
    ("a group refusal has the context of an aggregate", "engine.cho", "    var function = -3;", "    var function = 2;"),
    ("a group refusal names the wrong column", "engine.cho", "        column = cols[query.count_of(tree, 0) + query.count_of(tree, 1) + k - 1000];", "        column = cols[query.count_of(tree, 0) + query.count_of(tree, 1)];"),
    ("an order-by typed refusal has the abort code of an integer", "engine.cho", "    if status >= 5 {\n        // a typed key's cell", "    if status >= 50 {\n        // a typed key's cell"),
    ("an order-by refusal does not say which key", "engine.cho", "    a[k_err_cond()] = which;\n    a[k_err_fn()] = -2;", "    a[k_err_cond()] = 0;\n    a[k_err_fn()] = -2;"),
    ("an order-by refusal does not count the digits", "engine.cho", "    a[k_err_fn()] = -2;\n    if status == 7 {", "    a[k_err_fn()] = -2;\n    if status == 77 {"),
    ("a typed group column does not count in the type conflict", "query.cho", "        if gkind_at(q, k) != 0 && cols[name] == column {", "        if false && gkind_at(q, k) != 0 && cols[name] == column {"),
    ("a group column is a type conflict with itself", "query.cho", "                return (gkind_at(q, k), name);", "                return (gkind_at(q, k) + 1, name);"),
    ("an order key's type is one more in the conflict", "query.cho", "                return (order_flags(q, k) / 2, order_name(q, k));", "                return (order_flags(q, k) / 2 + 1, order_name(q, k));"),
    ("an order key that is not an integer counts as one", "query.cho", "        if order_flags(q, k) / 2 != 0 && cols[order_name(q, k)] == column {", "        if order_flags(q, k) / 2 == 0 && cols[order_name(q, k)] == column {"),
    ("a group column's type is the last one given", "query.cho", "    return vec.get(q.gkinds, j);", "    return vec.get(q.gkinds, vec.size(q.gkinds) - 1);"),
    # the row sorter (sorter.cho)
    ("a typed order key is read as an integer", "sorter.cho", "    if kind == 1 {\n        return query.parse_int(data);\n    }\n    return query.parse_typed(data, kind);", "    return query.parse_int(data);"),
    ("an integer order key is read as typed", "sorter.cho", "    if kind == 1 {\n        return query.parse_int(data);\n    }\n    return query.parse_typed(data, kind);", "    return query.parse_typed(data, kind);"),
    ("a typed order key is compared as text", "sorter.cho", "        if keys[3 * j + 2] != 0 {\n            let vx", "        if keys[3 * j + 2] == 1 {\n            let vx"),
    ("a typed first key's prefix is its text", "sorter.cho", "            if keys[2] != 0 {\n                pre[i] = vec.get(s.acc, i * stride + 2);", "            if keys[2] == 1 {\n                pre[i] = vec.get(s.acc, i * stride + 2);"),
    ("a typed key is held as text when rows are cut back", "sorter.cho", "                if keys[3 * j + 2] != 0 {\n                    fresh_rows = vec.push(heap, fresh_rows, a);", "                if keys[3 * j + 2] == 1 {\n                    fresh_rows = vec.push(heap, fresh_rows, a);"),
    ("a typed key is held as text by the fast hook", "sorter.cho", "        if contents(g.memo)[6 + 3 * j] != 0 {\n            let (v, bad) = key_value(", "        if contents(g.memo)[6 + 3 * j] == 1 {\n            let (v, bad) = key_value("),
    ("the early rejection reads a typed key as text", "sorter.cho", "        if keys[3 * j + 2] != 0 {\n            let (v, bad) = key_value(record[first..end], keys[3 * j + 2]);", "        if keys[3 * j + 2] == 1 {\n            let (v, bad) = key_value(record[first..end], keys[3 * j + 2]);"),
    ("a typed order cell that is not the type is held", "sorter.cho", "            } else if bad != 0 {\n                status = bad;\n                which = j;", "            } else if false {\n                status = bad;\n                which = j;"),
    ("a typed order key is refused as another key", "sorter.cho", "            } else if bad != 0 {\n                status = bad;\n                which = j;", "            } else if bad != 0 {\n                status = bad;\n                which = 0;"),
    # the plan (table.cho)
    ("a group column's type is not kept", "table.cho", "                q = query.add_gkind(heap, q, t);", "                q = query.add_gkind(heap, q, 0);"),
    ("the type of --group is split off from --select too", "table.cho", "    if slot == 2 {\n        let (c2, t2, b2) = split_types(heap, given, true);", "    if slot != 7 {\n        let (c2, t2, b2) = split_types(heap, given, true);"),
    ("an order key's type is not in its flags", "table.cho", "        typed_flags = vec.push(heap, typed_flags, fv + 2 * kt);", "        typed_flags = vec.push(heap, typed_flags, fv);"),
    ("an order key's type is its direction", "table.cho", "        typed_flags = vec.push(heap, typed_flags, fv + 2 * kt);", "        typed_flags = vec.push(heap, typed_flags, fv + kt);"),
    ("a bad scale in --group is not told", "table.cho", "    if bad_item >= 0 && listed {", "    if false && bad_item >= 0 && listed {"),
    ("a bad scale in --order-by is not told", "table.cho", "    if bad_scale >= 0 {", "    if false && bad_scale >= 0 {"),
    ("an escaped colon in --group stays escaped", "table.cho", "                if unescape && d == ':' {", "                if false && d == ':' {"),
    ("a typed group is written as its key bytes in csv", "table.cho", "                            if query.gkind_at(tree, j) != 0 {\n                                pending = agg.put_key(heap, pending, groups, tree, x, j);", "                            if query.gkind_at(tree, j) == 99 {\n                                pending = agg.put_key(heap, pending, groups, tree, x, j);"),
    ("a refusal of a group has the context where", "table.cho", "    if function == -3 {\n        return \"group\";\n    }", "    if function == -4 {\n        return \"group\";\n    }"),
    ("a group refusal takes the scale of an aggregate", "table.cho", "            if c.err_fn == -3 {\n                scale = query.gkind_at(tree, c.err_cond) - 2;", "            if c.err_fn == -4 {\n                scale = query.gkind_at(tree, c.err_cond) - 2;"),
    ("an order-by refusal takes the scale of an aggregate", "table.cho", "            } else if c.err_fn == -2 {\n                scale = query.order_flags(tree, c.err_cond) / 2 - 2;", "            } else if c.err_fn == -4 {\n                scale = query.order_flags(tree, c.err_cond) / 2 - 2;"),
    ("the overflow group is written as key bytes", "table.cho", "                held = agg.put_key(heap, held, gr, tree, x, j);", "                held = buffer.append(heap, held, agg.key_field(gr, x, j));"),
]

# Equivalent: a mutant here would survive, and is not in MUTANTS (the reason is the stage's, docs/numbers.md N5): none yet.
EQUIVALENT = []

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
