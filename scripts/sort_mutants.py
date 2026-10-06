#!/usr/bin/env python3
"""Mutation check of `--order-by` (docs/sort.md): each mutant is one source file of tools/table with one deliberate
defect in the sorter, the plan or the way the sorted rows are written, rebuilt, and run against test_sort, the memory
tests and the rules tests. A mutant is killed when a test fails or the build refuses it.

    python3 scripts/sort_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_sort", "test_memory", "test_rules"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # sorter.reject: the bounded top-N's early drop
    ("a row worse than the last wanted is kept and a better one dropped", "sorter.ls", "    if decided == 1 {\n        return 0;\n    }\n    return 1;", "    if decided == 2 {\n        return 0;\n    }\n    return 1;"),
    ("a descending key is compared ascending when dropping", "sorter.ls", "        if decided == 0 {\n            if keys[3 * j + 1] == 1 {\n                c = 0 - c;\n            }", "        if decided == 0 {\n            if false {\n                c = 0 - c;\n            }"),
    ("an integer key after the deciding one is not read when dropping", "sorter.ls", "            if bad != 0 {\\n                return 0;\\n            }".replace("\\n", "\n"), "            if bad != 0 && decided == 0 {\n                return 0;\n            }"),
    ("an integer that is a text sorts as a number, in reject", "sorter.ls", "            let (v, bad) = query.parse_int(record[first..end]);\n            if bad != 0 {\n                return 0;", "            let (v, bad) = query.parse_int(record[first..end]);\n            if false {\n                return 0;"),
    # sorter.add
    ("--max-sort-rows allows one more", "sorter.ls", "    if count >= max_rows {", "    if count > max_rows {"),
    ("the state bound is not kept", "sorter.ls", "    } else if size + len(record) + 8 * stride * (count + 1) > max_state {", "    } else if size + len(record) + 8 * stride * (count + 1) > max_state + 100000000 {"),
    ("a cell that is not an integer is taken", "sorter.ls", "            if bad == 1 {\n                status = 3;", "            if false && bad == 1 {\n                status = 3;"),
    ("an integer past 64 bits is not an integer", "sorter.ls", "            } else if bad == 2 {\n                status = 4;", "            } else if bad == 2 {\n                status = 3;"),
    ("the key that refused is the first", "sorter.ls", "            if bad == 1 {\n                status = 3;\n                which = j;", "            if bad == 1 {\n                status = 3;\n                which = 0;"),
    ("the doubled quotes of a key are kept", "sorter.ls", "            s2 = query.unquote(heap, s2, record[first..end]);", "            s2 = buffer.append(heap, s2, record[first..end]);"),
    ("an unquoted key is compared where it was quoted", "sorter.ls", "        let unquote = cells[3 * column + 2] == 1 && index_of_byte(record[first..end], byte_of(34)) >= 0;", "        let unquote = false;"),
    # the order
    ("rows with equal keys come out in reverse order", "sorter.ls", "    return vec.get(s.rows, x * stride + 2) < vec.get(s.rows, y * stride + 2);", "    return vec.get(s.rows, x * stride + 2) > vec.get(s.rows, y * stride + 2);"),
    ("a descending key is compared ascending", "sorter.ls", "            if keys[3 * j + 1] == 1 {\n                return c > 0;\n            }", "            if keys[3 * j + 1] == 1 {\n                return c < 0;\n            }"),
    ("a descending first key is ascending by its prefix", "sorter.ls", "        if keys[1] == 1 {\n            return pre[x] > pre[y];\n        }", "        if false {\n            return pre[x] > pre[y];\n        }"),
    ("the prefix of an integer first key is its text", "sorter.ls", "            if keys[2] == 1 {\n                pre[i] = vec.get(s.rows, i * stride + 3);", "            if false {\n                pre[i] = vec.get(s.rows, i * stride + 3);"),
    ("the merge takes the later of two equal runs first", "sorter.ls", "(q >= hi || !before(s, keys, pre, order[q], order[p]))", "(q >= hi || before(s, keys, pre, order[q], order[p]))"),
    ("the second key of two is not looked at", "sorter.ls", "    var j = 0;\n    while j < s.nk {\n        var c = 0;\n        if keys[3 * j + 2] == 1 {\n            let vx", "    var j = 0;\n    while j < 1 {\n        var c = 0;\n        if keys[3 * j + 2] == 1 {\n            let vx"),
    # the cut back
    ("the cut back keeps one row too few", "sorter.ls", "        while i < cap && i < count {", "        while i < cap - 1 && i < count {"),
    ("the cut back forgets the rows' numbers", "sorter.ls", "            fresh_rows = vec.push(heap, fresh_rows, vec.get(or.rows, x * stride + 2));", "            fresh_rows = vec.push(heap, fresh_rows, 0);"),
    ("the cut back keeps the offsets of the old buffer", "sorter.ls", "                    fresh_rows = vec.push(heap, fresh_rows, at + a - old_off);", "                    fresh_rows = vec.push(heap, fresh_rows, a);"),
    ("the cut back loses an unquoted key", "sorter.ls", "                    fresh_side = buffer.append(heap, fresh_side, key_bytes(or, x, j));", "                    fresh_side = buffer.append(heap, fresh_side, \"\");"),
    # engine.order_row
    ("a row that fails --where is held", "engine.ls", "    let (verdict, e2, k2) = screen(heap, tree, cols, record, cells, escr, kept, opened, a);\n    if verdict != 1 {\n        return (held, e2, k2);", "    let (verdict, e2, k2) = screen(heap, tree, cols, record, cells, escr, kept, opened, a);\n    if verdict < 0 {\n        return (held, e2, k2);"),
    ("the held rows are never cut back", "engine.ls", "            full = or.cap > 0 && sorter.held(or) >= 2 * or.cap + 1;", "            full = false;"),
    ("a refusal of a key is numbered one high", "engine.ls", "    a[k_abort()] = 8 + status;", "    a[k_abort()] = 9 + status;"),
    ("a key that is not an integer is said to be in the condition", "engine.ls", "    a[k_err_fn()] = -2;", "    a[k_err_fn()] = -1;"),
    ("a bound on the rows is a bound on the bytes", "engine.ls", "    if status == 1 {\n        a[k_abort()] = 20;", "    if status == 1 {\n        a[k_abort()] = 21;"),
    # table.ls: the wanted rows, the plan, the writing
    ("a page does not keep the row that says there is more", "table.ls", "            wanted = from + limit + 1;", "            wanted = from + limit;"),
    ("a bound too small for the page does not fall back to a full sort", "table.ls", "        if wanted > 0 && 2 * wanted + 1 > max_sort_rows {\n            wanted = 0;", "        if false && wanted > 0 && 2 * wanted + 1 > max_sort_rows {\n            wanted = 0;"),
    ("--top is not a cut of the sorted rows", "table.ls", "    var end = n;\n    if top > 0 && top < n {\n        end = top;\n    }\n    var pending = rows;", "    var end = n;\n    if false {\n        end = top;\n    }\n    var pending = rows;"),
    ("a page starts one row late", "table.ls", "    let ord = sorter.order(heap, held, keys);\n    var pos = from;", "    let ord = sorter.order(heap, held, keys);\n    var pos = from + 1;"),
    ("the next of a full page is one past", "table.ls", "            a[engine.k_more()] = 1;\n            a[engine.k_next()] = pos;\n            going = false;\n        } else {\n            borrow ord", "            a[engine.k_more()] = 1;\n            a[engine.k_next()] = pos + 1;\n            going = false;\n        } else {\n            borrow ord"),
    ("the next of a page ended by the byte budget is the rows read", "table.ls", "                if a[engine.k_more()] == 1 {\n                    a[engine.k_next()] = pos;\n                }", "                if a[engine.k_more()] == 1 {\n                    a[engine.k_next()] = a[engine.k_records()] - 1;\n                }"),
    ("the rows before `from` are skipped as they are read", "table.ls", "    if mode == 2 || ordering {\n        row_from = 0;", "    if mode == 2 {\n        row_from = 0;"),
    ("--order-by does not make a selection", "table.ls", "    } else if has_select || has_where || has_order {", "    } else if has_select || has_where {"),
    ("--order-by and --group do not conflict", "table.ls", "    if grouped && has_order {", "    if false && grouped && has_order {"),
    ("--max-rows stops a sort quietly in json", "table.ls", "    } else if (as_csv || query.count_of(tree, 4) > 0) && c.capped {", "    } else if as_csv && c.capped {"),
    ("a descending integer key is read as ascending", "table.ls", "                                                    contents(ow)[3 * j + 1] = query.order_flags(tree, j) % 2;", "                                                    contents(ow)[3 * j + 1] = query.order_flags(tree, j) / 2;"),
    ("a descending key is not descending", "table.ls", "        } else if was_start && c == '-' && !minus_done {\n            flag = 1;", "        } else if was_start && c == '-' && !minus_done {\n            flag = 0;"),
    (":int is descending", "table.ls", "            flag = flag + 2;", "            flag = flag + 1;"),
    ("an escaped minus is not allowed", "table.ls", "            if d == '-' && was_start || d == ':' {", "            if d == ':' {"),
    ("an escaped colon is not allowed", "table.ls", "            if d == '-' && was_start || d == ':' {", "            if d == '-' && was_start {"),
    ("an unknown key is said to be in --agg", "table.ls", "                flag = \"--order-by\";", "                flag = \"--agg\";"),
    ("--max-sort-rows is not checked", "table.ls", "    if sort_rows_most < 1 || sort_rows_most > 20000000 {", "    if sort_rows_most < 0 || sort_rows_most > 200000000 {"),
    ("threads read a sort", "table.ls", "threads > 1 && !ordering && (mode == 2 || row_from == 0)", "threads > 1 && (mode == 2 || row_from == 0)"),
    # query.ls
    ("the flags of a key are its name's", "query.ls", "    return vec.get(q.meta, 6 + 2 * k);", "    return vec.get(q.meta, 5 + 2 * k);"),
]

# Not here, and why: mutants that change nothing a test can see.
#  - a row equal to the last wanted is kept when dropping (`return 1` for `return 0` at the end of `reject`): it is
#    cut back at the next cut, by the rows' numbers; the answer is the same and only the memory is not;
#  - the first rows are not told apart from the sort of everything (`--top` not a cut of what is held, in `wanted`): the
#    rows past `top` are held and never written;
#  - the rows that were rejected are kept (`reject` always answering 0): the cut back drops them;
#  - a prefix of 6 bytes for 7: the full keys settle what the prefix does not.


if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
