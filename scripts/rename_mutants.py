#!/usr/bin/env python3
"""Mutation check of `--rename` and `select a as b` (docs/or-and-columns.md section 9, G-B1 and G-MUT, slice B1): each mutant is the reader of the list, its application to the labels, the place
the labels are written, the refusals or the query front end broken in one way, rebuilt and run against test_rename_b1 (the reference of the list, the labels in three formats, the SQLite oracle,
the refusals and their repairs, the query form against the flag form, the threads, MCP, the fuzz), test_rules and test_query (the rule table and the pinned places `as` can fail) and test_skill
(what the help says). A mutant is killed when a test fails or the build refuses it; one that survives is a hole in the gates, or an equivalent mutant, which is then explained under EQUIVALENT
and left out of MUTANTS.

    python3 scripts/rename_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). The counts of the generated cases are lowered here (RENAME_B1_*) so that a mutant costs a minute; a mutant that
survives is run again at the full counts before it is called a survivor. See mutlib.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mutlib  # noqa: E402

for key, value in (("RENAME_B1_SOUPS", "500"), ("RENAME_B1_PLANS", "120"), ("RENAME_B1_QPLANS", "80"), ("RENAME_B1_SQL", "60")):
    os.environ.setdefault(key, value)

TESTS = ["test_rename_b1", "test_rules", "test_query", "test_skill", "test_describe"]

MUTANTS = [
    # ---- the list (relabel.parse)
    ("an escaped equals sign is not an escape", "relabel.cho", r"return c == ',' || c == '\\' || c == '=' || c == '#';", r"return c == ',' || c == '\\' || c == '#';"),
    ("an escaped # is not an escape", "relabel.cho", r"return c == ',' || c == '\\' || c == '=' || c == '#';", r"return c == ',' || c == '\\' || c == '=';"),
    ("any byte may follow a backslash", "relabel.cho", r"return c == ',' || c == '\\' || c == '=' || c == '#';", "return c >= 0;"),
    ("an item without an equals sign is accepted", "relabel.cho", "if eq < 0 {\n            return (items, 1, end, 2);", "if eq < 0 {\n            return (items, 0, 0, 0);"),
    ("the offset of a missing equals sign is the start of the item", "relabel.cho", "return (items, 1, end, 2);", "return (items, 1, start, 2);"),
    ("an empty target is accepted", "relabel.cho", "if eq == start {", "if eq == start && false {"),
    ("the offset of an empty target is the next byte", "relabel.cho", "return (items, 1, start, 3);", "return (items, 1, start + 1, 3);"),
    ("an empty new label is accepted", "relabel.cho", "if end == eq + 1 {", "if end == eq + 1 && false {"),
    ("the offset of an empty new label is the equals sign", "relabel.cho", "return (items, 1, eq + 1, 4);", "return (items, 1, eq, 4);"),
    ("a bad escape is reported at the next byte", "relabel.cho", "return (items, 1, i, 1);", "return (items, 1, i + 1, 1);"),
    ("the last equals sign splits an item", "relabel.cho", "if c == '=' && eq < 0 {", "if c == '=' {"),
    ("a position needs two digits", "relabel.cho", "eq - start >= 2", "eq - start >= 3"),
    ("a position may have ten digits", "relabel.cho", "eq - start <= 10", "eq - start <= 11"),
    ("a position is not checked for digits", "relabel.cho", "&& digits(text[start + 1..eq]) {", "{"),
    ("a position is counted from 0", "relabel.cho", "position = number(text[start + 1..eq]);", "position = number(text[start + 1..eq]) + 1;"),
    ("4097 items are accepted", "relabel.cho", "if count > most_items() {", "if count > most_items() + 1 {"),
    ("the ceiling is 4095 items", "relabel.cho", "return 4096;", "return 4095;"),
    # ---- its application (relabel.apply)
    ("an ambiguous label is an unknown one", "relabel.cho", "map.set_value_at(iw, found, 0 - 2);", "map.set_value_at(iw, found, 0 - 1);"),
    ("an unknown target is passed over", "relabel.cho", "} else if column < 0 {\n                why = 1;", "} else if column < 0 {\n                why = 0;"),
    ("a column renamed twice passes", "relabel.cho", "if prior >= 0 {", "if prior >= 0 && false {"),
    ("every renamed column takes the first new label", "relabel.cho", "contents(ow)[column] = k;", "contents(ow)[column] = 0;"),
    ("the new label is not unescaped", "relabel.cho", "let fresh = unescaped(heap, text[vec.get(il, 6 * item + 4)..vec.get(il, 6 * item + 5)]);",
     "let fresh = buffer.append(heap, buffer.empty(heap, 16), text[vec.get(il, 6 * item + 4)..vec.get(il, 6 * item + 5)]);"),
    ("the target is not unescaped", "relabel.cho", "let target = unescaped(heap, text[vec.get(il, b + 2)..vec.get(il, b + 3)]);",
     "let target = buffer.append(heap, buffer.empty(heap, 16), text[vec.get(il, b + 2)..vec.get(il, b + 3)]);"),
    ("a position past the last column is dropped without a word", "relabel.cho", "if p >= 1 && p <= width {", "if p >= 1 && p <= width + 1 {"),
    ("a position is looked up as a label", "relabel.cho", "if vec.get(il, b) == 1 {", "if vec.get(il, b) == 9 {"),
    # ---- where the labels are written (frame.prepare)
    ("a rename of rows is not applied", "frame.cho", "if len(query.rename_text(tree)) > 0 {\n            // a rename: the labels of the selection first", "if false && len(query.rename_text(tree)) > 0 {\n            // a rename: the labels of the selection first"),
    ("a rename of groups is not applied", "frame.cho", "        if len(query.rename_text(tree)) > 0 {\n            let (l3, e3, a3, b3) = renamed(heap, tree, labels, lends);\n            labels = l3;\n            lends = e3;\n            if a3 != 0 {\n                abort = a3;\n                bad = b3;\n            }\n        }\n        head = buffer.push(heap, head, byte_of('['));\n        m = 0;",
     "        if false && len(query.rename_text(tree)) > 0 {\n            let (l3, e3, a3, b3) = renamed(heap, tree, labels, lends);\n            labels = l3;\n            lends = e3;\n            if a3 != 0 {\n                abort = a3;\n                bad = b3;\n            }\n        }\n        head = buffer.push(heap, head, byte_of('['));\n        m = 0;"),
    ("the abort of a rename is the next rule's", "frame.cho", "return (labels, lends, 61 + why, at * 4096 + first);", "return (labels, lends, 62 + why, at * 4096 + first);"),
    ("the item at fault is decoded wrongly", "frame.cho", "return (labels, lends, 61 + why, at * 4096 + first);", "return (labels, lends, 61 + why, at * 4095 + first);"),
    ("a rename of rows writes the labels twice for json lines", "frame.cho", "} else if !query.reporting(tree) && len(query.rename_text(tree)) > 0 {", "} else if !query.reporting(tree) && len(query.rename_text(tree)) > 99 {"),
    # ---- the plan and the flag (query.cho, inputs.cho, table.cho)
    ("the plan keeps no rename", "query.cho", "rename: buffer.append(heap, rename, text) };", "rename: rename };"),
    ("--rename is never looked up", "inputs.cho", "bytes.starts_with(a, \"--rename\")", "bytes.starts_with(a, \"--renamx\")"),
    ("--rename is never given", "inputs.cho", "has_rename: has_rename, select_text", "has_rename: false, select_text"),
    ("a rename is allowed with the shape", "table.cho", "e = relfail.check(heap, e, rename_t, mode == 0, has_report, inp.has_query);", "e = relfail.check(heap, e, rename_t, mode == 99, has_report, inp.has_query);"),
    ("a rename is allowed with the report", "table.cho", "e = relfail.check(heap, e, rename_t, mode == 0, has_report, inp.has_query);", "e = relfail.check(heap, e, rename_t, mode == 0, false, inp.has_query);"),
    ("the plan is not given the rename", "table.cho", "tree = query.set_rename(heap, tree, rename_t);", "tree = query.set_rename(heap, tree, \"\");"),
    ("--query takes --rename", "table.cho", "    if inp.has_rename {\n        clash = buffer.append(heap, clash, \" --rename\");", "    if false && inp.has_rename {\n        clash = buffer.append(heap, clash, \" --rename\");"),
    ("the rename of a query is not passed on", "table.cho", "qtrans.section(t, 6), qtrans.section(t, 7));", "qtrans.section(t, 6), qtrans.section(t, 8));"),
    ("--discover takes --rename", "table.cho", "|| inp.has_from || inp.has_rename, as_csv", "|| inp.has_from, as_csv"),
    ("a rename refusal at the header is made as the keys are", "table.cho", "if c.abort >= 62 {", "if c.abort >= 99 {"),
    ("the repeated target is not made at the header", "table.cho", "if c.abort >= 60 && c.abort <= 64 {", "if c.abort >= 60 && c.abort <= 63 {"),
    # ---- the refusals (relfail.cho)
    ("rename.syntax says the byte after", "relfail.cho", "w = fail.detail_int(heap, w, \"offset\", at);", "w = fail.detail_int(heap, w, \"offset\", at + 1);"),
    ("rename.syntax expects the item", "relfail.cho", "return \"a target before the =\";", "return \"TARGET=NEW\";"),
    ("the item of a refusal is counted from 0", "relfail.cho", "w = fail.detail_int(heap, w, \"item\", at + 1);", "w = fail.detail_int(heap, w, \"item\", at);"),
    ("the first renaming is counted from 0", "relfail.cho", "w = fail.detail_int(heap, w, \"first\", bad % 4096 + 1);", "w = fail.detail_int(heap, w, \"first\", bad % 4096);"),
    ("the places of an ambiguous label are counted from 0", "relfail.cho", "w = json.put_int(heap, w, c + 1);", "w = json.put_int(heap, w, c);"),
    ("six case variants are offered", "relfail.cho", "while c < width && kept < 5 {", "while c < width && kept < 6 {"),
    ("a case variant is compared in one direction", "relfail.cho", "if bytes.to_lower(int_of(a[i])) != bytes.to_lower(int_of(b[i])) {", "if int_of(a[i]) != bytes.to_lower(int_of(b[i])) {"),
    ("the repair writes the label as it is", "relfail.cho", "fixed = escaped_target(heap, fixed, buffer.bytes(labels)[cb..vec.get(lends, cc)]);", "fixed = buffer.append(heap, fixed, buffer.bytes(labels)[cb..vec.get(lends, cc)]);"),
    ("the repair drops the rest of the list", "relfail.cho", "fixed = buffer.append(heap, fixed, list[te..len(list)]);", "fixed = buffer.append(heap, fixed, list[te..te]);"),
    ("the repair does not escape an equals sign", "relfail.cho", "if c == ',' || c == '\\\\' || c == '=' || c == '#' && j == 0 {", "if c == ',' || c == '\\\\' || c == '#' && j == 0 {"),
    ("the repair is offered for --rename=LIST", "relfail.cho", " && cli.value_is_whole(parsed, table, \"rename\") {", " {"),
    ("51 labels are listed", "relfail.cho", "while j < width && j < 50 {", "while j < width && j < 51 {"),
    ("the list is not said to be truncated at 51", "relfail.cho", "width > 50)", "width > 51)"),
    # ---- the query front end (qparse.cho, qtrans.cho, qrepair.cho, qtext.cho)
    ("the token of an alias is off by one", "qparse.cho", "m[r + 7] = i + 1;", "m[r + 7] = i;"),
    ("a keyword is an alias", "qparse.cho", "if kind == qlex.t_id() && !keyword(q, m, i) || kind == qlex.t_qid() {", "if kind == qlex.t_id() || kind == qlex.t_qid() {"),
    ("a position is an alias", "qparse.cho", "if kind == qlex.t_id() && !keyword(q, m, i) || kind == qlex.t_qid() {", "if kind == qlex.t_id() && !keyword(q, m, i) || kind == qlex.t_qid() || kind == qlex.t_pos() {"),
    ("a bad alias is refused at its end", "qparse.cho", "return qarena.fail(m, 1, 31, qarena.tok_start(m, i));", "return qarena.fail(m, 1, 31, qarena.tok_end(m, i));"),
    ("the words after as are another", "qtext.cho", "return \"a name after as (a name that is a keyword is written \\\"like this\\\")\";", "return \"a name\";"),
    ("the place of a rename is off by one", "qtrans.cho", "text = buffer.push_nat(heap, text, item + 1);", "text = buffer.push_nat(heap, text, item + 2);"),
    ("a comma in an alias is not escaped", "qtrans.cho", "if c == ',' || c == '\\\\' || c == '#' && i == 0 {", "if c == '\\\\' || c == '#' && i == 0 {"),
    ("a backslash in an alias is not escaped", "qtrans.cho", "if c == ',' || c == '\\\\' || c == '#' && i == 0 {", "if c == ',' || c == '#' && i == 0 {"),
    ("a leading # in an alias is not escaped", "qtrans.cho", "if c == ',' || c == '\\\\' || c == '#' && i == 0 {", "if c == ',' || c == '\\\\' {"),
    ("the rename of a query is not announced", "qtrans.cho", "if renamed {\n        info = put_info(info, i_rename(), 1);", "if false {\n        info = put_info(info, i_rename(), 1);"),
    ("an alias does not win in the order by of rows", "qtrans.cho", "if hits == 1 {\n                    key_col = m[items + 8 * which + 2];", "if hits == 99 {\n                    key_col = m[items + 8 * which + 2];"),
    ("an alias does not win in the order by of a grouping", "qtrans.cho", "if hits == 1 {\n                    key_item = which;", "if hits == 99 {\n                    key_item = which;"),
    ("two aliases of one name are not refused in rows", "qtrans.cho", "if hits > 1 {\n                    qarena.fail(m, 3, 46, m[r + 6]);", "if hits > 99 {\n                    qarena.fail(m, 3, 46, m[r + 6]);"),
    ("two aliases of one name are not refused in a grouping", "qtrans.cho", "if hits > 1 {\n                    qarena.fail(m, 3, 46, where_key);", "if hits > 99 {\n                    qarena.fail(m, 3, 46, where_key);"),
    ("a sort label an earlier column has is accepted", "qtrans.cho", "shadowed = other_known && bytes.equal(buffer.bytes(orr), buffer.bytes(lr));", "shadowed = false;"),
    ("--sort names the header's name and not the alias", "qtrans.cho", "if a >= 0 {\n        return (decode(heap, q, m, a), true);", "if a >= 99 {\n        return (decode(heap, q, m, a), true);"),
    ("the repair of a grouping drops the alias of an aggregate", "qrepair.cho", "if m[items + 8 * i + 7] > 0 {", "if false {"),
    # ---- the rule table
    ("the hint of rename.syntax in the table is another", "finish.cho", "NEW at least one byte, with a backslash before a comma, a backslash, an equals sign or a # that is part of a name. Repair: none.;", "NEW at least two bytes, with a backslash before a comma, a backslash, an equals sign or a # that is part of a name. Repair: none.;"),
    ("rename.unknown-target exits 2 in the table", "finish.cho", "rename.unknown-target|3|sometimes|", "rename.unknown-target|2|sometimes|"),
    ("rename.ambiguous-target is repairable in the table", "finish.cho", "rename.ambiguous-target|8|never|", "rename.ambiguous-target|8|sometimes|"),
    ("output.duplicate-column does not say --rename", "finish.cho", "each aggregate once, or rename one with --rename. Repair: none.", "each aggregate once. Repair: none."),
]

# Equivalent mutants, left out of MUTANTS with the reason (each was tried, built and survived the suite, and the reason is why it must):
EQUIVALENT = [
    ("the plan of a worker thread has no rename", "query.cho", "rename: copy_buffer(heap, plan.rename) };", "rename: buffer.empty(heap, 1) };",
     "`duplicate` gives a worker its own plan, and the labels are written once, by the parent, in `frame.prepare` before any worker is started; no thread reads the rename, so a copy that drops it is not seen"),
    ("the labels of a header the rename refused are not written", "frame.cho", "while m < ng + na && abort == 0 {", "while m < ng + na {",
     "when a rename is refused `abort` is set and the header is never used (the refusal is made from the labels as they were), so writing it from them or not is not seen"),
    ("the header of a refused rename is written", "frame.cho", "while m < picked && abort == 0 {", "while m < picked {",
     "the same, for rows"),
]

if __name__ == "__main__":
    # the equivalent mutants are not run, but their sites must still be there (a stale entry is a lie about what was proved)
    stale = mutlib.check([e[:4] for e in EQUIVALENT])
    for p in stale:
        print("!! (equivalent) " + p)
    if stale:
        sys.exit(1)
    sys.exit(mutlib.main(MUTANTS, TESTS))
