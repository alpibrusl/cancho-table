#!/usr/bin/env python3
"""Mutation check of `or` and parentheses (docs/or-and-columns.md section 9, G-MUT, slices A0 and A1): each mutant is the grammar, the compiler of the branch program, its evaluation, the
refusal of a mix or the query front end broken in one way, rebuilt and run against test_or_a1 (the flag form: SQL and reference oracles, the program, the threads, the offsets, the fuzz),
test_or_a1_query (the query form) and test_or_a1_compat (what valid calls and today's refusals say). A mutant is killed when a test fails or the build refuses it; one that survives is a
hole in the gates, or an equivalent mutant, which is then explained under EQUIVALENT and left out of MUTANTS.

The mutants of slice A0 (the repair of an `or` of one column as an `in`: `expr.or_in`, `qparse.or_chain`, `qrepair.or_to_in`) are under EQUIVALENT: since slice A1 an `or` is not refused, so no
refusal of the binary reaches that code, and a defect in it cannot be seen from outside. The code is kept as the document asks (section 7: for any other client that builds an expression); the
sites are checked here so that a change to it is noticed, and the report of the pull request says that nothing exercises it.

    python3 scripts/or_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). The counts of the generated cases are lowered here (OR_A1_*) so that a mutant costs a minute, not five; a
mutant that survives is run again at the full counts before it is called a survivor. See mutlib.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mutlib  # noqa: E402

for key, value in (("NOT_A2_SQL", "400"), ("NOT_A2_REF", "400"), ("NOT_A2_PROGRAM", "500"), ("NOT_A2_SOUPS", "2000"), ("NOT_A2_FUZZ", "1500"), ("NOT_A2_QSOUPS", "1500"), ("NOT_A2_QPLANS", "150"), ("OR_A1_SQL", "500"), ("OR_A1_REF", "500"), ("OR_A1_PROGRAM", "700"), ("OR_A1_SOUPS", "2500"), ("OR_A1_FUZZ", "2000"), ("OR_A1_QSOUPS", "2500")):
    os.environ.setdefault(key, value)

TESTS = ["test_not_a2_query", "test_not_a2", "test_or_a1_query", "test_or_a1", "test_not_a2_compat", "test_or_a1_compat"]

MUTANTS = [
    # ---- the grammar of --where (orparse.cho)
    ("an or is an and", "orparse.cho", "bytes.equal(buffer.bytes(wr), \"or\") {\n            c = 2;", "bytes.equal(buffer.bytes(wr), \"or\") {\n            c = 1;"),
    ("a quoted or is a connective", "orparse.cho", "if status == 0 && quoted == 0 && bytes.equal(buffer.bytes(wr), \"and\") {", "if status == 0 && bytes.equal(buffer.bytes(wr), \"and\") {"),
    ("the mix is accepted: the first connective decides the level", "orparse.cho", "} else if kind != c && kind != 4 {", "} else if false {"),
    ("the mix is refused at the end of the level, not at the connective", "orparse.cho", "what = mixed();\n                            at = i;", "what = mixed();\n                            at = start;"),
    ("a mix is not refused in tolerant mode only: the repair reads the mix as refused", "orparse.cho", "if tolerant {\n                            kind = 4;", "if false {\n                            kind = 4;"),
    ("the ceiling of parentheses is 9", "orparse.cho", "if depth >= depth_limit() {", "if depth >= 9 {"),
    ("the ceiling of parentheses is 7", "orparse.cho", "pub fn depth_limit() -> [] int {\n    return 8;", "pub fn depth_limit() -> [] int {\n    return 7;"),
    ("the ceiling of conditions is 65", "orparse.cho", "if leaves >= 64 {", "if leaves >= 65 {"),
    ("a group needs no closing parenthesis", "orparse.cho", "if i < len(src) && int_of(src[i]) == ')' {\n                        i = i + 1;", "if true {\n                        i = i + 1;"),
    ("a stray ) after the expression is accepted", "orparse.cho", "if i < len(src) {\n            what = 4;\n            at = i;\n        }\n    }\n    plan = query.bump", "if false {\n            what = 4;\n            at = i;\n        }\n    }\n    plan = query.bump"),
    ("the words after a unit inside a group are the end of the expression's", "orparse.cho", "if depth == 0 {\n                        what = 4;\n                    } else {\n                        what = 22;\n                    }", "what = 4;"),
    ("an or inside a group is not noticed (the plan is a conjunction)", "orparse.cho", "flowed = o2 || group_kind == 6;", "flowed = flowed || group_kind == 6;"),
    ("a plan with an or carries no program", "orparse.cho", "if what == 0 && flowed {", "if what == 0 && false {"),
    # ---- the compiler of the program
    ("the exits of a leaf are swapped", "orparse.cho", "vec.set(flow, 2 * leaf, t);\n        vec.set(flow, 2 * leaf + 1, f);", "vec.set(flow, 2 * leaf, f);\n        vec.set(flow, 2 * leaf + 1, t);"),
    ("an or is compiled as an and", "orparse.cho", "        if kind == 1 {\n            if j + 1 < count {\n                emit(tr, flow, kid, next, f);", "        if kind != 1 {\n            if j + 1 < count {\n                emit(tr, flow, kid, next, f);"),
    ("the next of an and is the node, not its first leaf", "orparse.cho", "next = first_leaf(tr, vec.get(tr, at + 2 * j + 3));", "next = vec.get(tr, at + 2 * j + 3);"),
    ("an and that is false goes on to the next unit", "orparse.cho", "emit(tr, flow, kid, next, f);", "emit(tr, flow, kid, next, next);"),
    ("the last unit of an or is false when it is false (it goes to the next, of the parent)", "orparse.cho", "emit(tr, flow, kid, t, next);", "emit(tr, flow, kid, t, t);"),
    ("the first leaf of a group is its last", "orparse.cho", "return first_leaf(tr, vec.get(tr, node + 1));", "return vec.get(tr, node + 1);"),
    # ---- the evaluation (branch.cho, engine.cho, query.cho)
    ("the exits are read for the other verdict", "expr.cho", "let next = query.flow_at(plan, k, 1 - verdict);", "let next = query.flow_at(plan, k, verdict);"),
    ("a kept row is dropped", "expr.cho", "if next == 0 - 1 {\n            return (1, 0, scr);", "if next == 0 - 1 {\n            return (0, 0, scr);"),
    ("a dropped row is kept", "expr.cho", "if next == 0 - 2 {\n            return (0, 0, scr);", "if next == 0 - 2 {\n            return (1, 0, scr);"),
    ("no short-circuit: every condition is tried in order", "expr.cho", "k = next;", "k = k + 1;\n        if k >= query.count_of(plan, 1) {\n            return (verdict, 0, scr);\n        }"),
    ("a refused cell is treated as false", "expr.cho", "if verdict >= 2 {\n            return (verdict, k, scr);\n        }", "if verdict >= 20 {\n            return (verdict, k, scr);\n        }\n        if verdict >= 2 {\n            verdict = 0;\n        }"),
    ("a quoted cell is not unquoted before the program's condition reads it", "expr.cho", "scr = query.unquote(heap, scr, record[first..last]);\n            borrow scr as &sr in {\n                verdict = holds(plan, k, buffer.bytes(sr));\n            }\n        } else {\n            verdict = holds(plan, k, record[first..last]);\n        }\n        if verdict >= 2 {", "scr = query.unquote(heap, scr, record[first..first]);\n            borrow scr as &sr in {\n                verdict = holds(plan, k, buffer.bytes(sr));\n            }\n        } else {\n            verdict = holds(plan, k, record[first..last]);\n        }\n        if verdict >= 2 {"),
    ("the program is not used: a plan with an or is run as a conjunction", "expr.cho", "if query.flowed(plan) {\n        return eval_program(", "if false {\n        return eval_program("),
    ("a thread's plan has no program", "query.cho", "sort: copy_buffer(heap, plan.sort), flow: copy_vec(heap, plan.flow) };", "sort: copy_buffer(heap, plan.sort), flow: vec.empty(heap, 2, 0) };"),
    ("the program is not copied into the plan", "query.cho", "flow: copy_vec(heap, program) };", "flow: vec.empty(heap, 2, 0) };"),
    ("adding an aggregate drops the program", "query.cho", "aggs: a, meta: meta, gkinds: gkinds, sort: sort, flow: flow };", "aggs: a, meta: meta, gkinds: gkinds, sort: sort, flow: vec.empty(heap, 2, 0) };", [("query.cho", "var a = vec.push(heap, aggs, function);", "vec.drop(heap, flow);\n    var a = vec.push(heap, aggs, function);")]),
    ("adding a name drops the program", "query.cho", "nkinds: vec.push(heap, nkinds, position), conds: conds, lits: lits, ltext: ltext, aggs: aggs, meta: meta, gkinds: gkinds, sort: sort, flow: flow }, at);", "nkinds: vec.push(heap, nkinds, position), conds: conds, lits: lits, ltext: ltext, aggs: aggs, meta: meta, gkinds: gkinds, sort: sort, flow: vec.empty(heap, 2, 0) }, at);", [("query.cho", "var n = buffer.append(heap, names, given);", "vec.drop(heap, flow);\n    var n = buffer.append(heap, names, given);")]),
    ("an order key drops the program", "query.cho", "gkinds: gkinds, sort: sort, flow: flow }, 4, 1);", "gkinds: gkinds, sort: sort, flow: vec.empty(heap, 2, 0) }, 4, 1);", [("query.cho", "var m = vec.push(heap, meta, name);\n    m = vec.push(heap, m, flags);", "vec.drop(heap, flow);\n    var m = vec.push(heap, meta, name);\n    m = vec.push(heap, m, flags);")]),
    ("the JSON-lines output plan drops the program", "jout.cho", "gkinds: vec.push(heap, gkinds, 0), sort: sort, flow: flow };", "gkinds: vec.push(heap, gkinds, 0), sort: sort, flow: vec.empty(heap, 2, 0) };", [("jout.cho", "let query.Query { names, nends, nkinds, conds, lits, ltext, aggs, meta, gkinds, sort, flow } = q;", "let query.Query { names, nends, nkinds, conds, lits, ltext, aggs, meta, gkinds, sort, flow } = q;\n    vec.drop(heap, flow);")]),
    # ---- the refusal of a mix and its two readings
    ("the flag form's mix is a plain syntax error", "table.cho", "if what == orparse.mixed() {", "if what == 31 {"),
    ("the mix repair offers one reading only (the first)", "wrepair.cho", "if ok1 {\n            borrow r1 as &rr in {", "if false {\n            borrow r1 as &rr in {"),
    ("the mix repair offers one reading only (the second)", "wrepair.cho", "if ok0 {\n            borrow r0 as &rr in {", "if false {\n            borrow r0 as &rr in {"),
    ("the mix repair is offered on the wrong argument", "wrepair.cho", "o = fail.choose_option_replacing(heap, o, args, where_at, text);", "o = fail.choose_option_replacing(heap, o, args, where_at + 1, text);"),
    ("--where= loses its flag in the mix repair", "wrepair.cho", "buffer.append(heap, buffer.empty(heap, 64), \"--where=\")", "buffer.append(heap, buffer.empty(heap, 64), \"\")"),
    ("the form of the flag is mistaken in the mix repair", "wrepair.cho", "if whole {\n        o = fail.choose_option_replacing", "if !whole {\n        o = fail.choose_option_replacing"),
    ("the reading by precedence does not close its and-runs", "orparse.cho", "            if e > j {\n                o = buffer.push(heap, o, byte_of('('));\n            }", "            if false {\n                o = buffer.push(heap, o, byte_of('('));\n            }"),
    ("the reading from the left does not wrap when the connective changes", "orparse.cho", "} else if c != cur {\n            var wrapped", "} else if false {\n            var wrapped"),
    ("the two readings are the same reading", "orparse.cho", "r1 = render(heap, buffer.empty(heap, len(src) + 16), src, tr, node, 1);", "r1 = render(heap, buffer.empty(heap, len(src) + 16), src, tr, node, 0);"),
    ("a reading that passes the depth is offered", "orparse.cho", "ok0 = whole(heap, buffer.bytes(rr));", "ok0 = true;"),
    ("two readings of one text are both offered", "orparse.cho", "if bytes.equal(buffer.bytes(r0r), buffer.bytes(rr)) {", "if bytes.equal(buffer.bytes(r0r), buffer.bytes(rr)) && false {"),
    ("a group is rendered without its parentheses", "orparse.cho", "o = buffer.push(heap, o, byte_of('('));\n        o = render(heap, o, src, tr, vec.get(tr, node + 1), policy);", "o = render(heap, o, src, tr, vec.get(tr, node + 1), policy);"),
    # ---- --explain
    ("the exits are not explained", "explain.cho", "if query.flowed(tree) {\n            o = explainor.put_exits(heap, o, tree, k);", "if false {\n            o = explainor.put_exits(heap, o, tree, k);"),
    ("the exits are explained swapped", "explainor.cho", "b = put_exit(heap, b, query.flow_at(tree, k, 0));", "b = put_exit(heap, b, query.flow_at(tree, k, 1));"),
    ("accept is reject in the explanation", "explainor.cho", "return buffer.append(heap, o, \"\\\"accept\\\"\");", "return buffer.append(heap, o, \"\\\"reject\\\"\");"),
    ("the note about an in is given for !=", "explainor.cho", "query.cond_at(tree, k, 0) == 0 && query.cond_at(tree, k, 2) == 0;", "query.cond_at(tree, k, 0) == 0;"),
    ("the note about an in is given for two columns", "explainor.cho", "return query.kind_at(tree, a) == query.kind_at(tree, b) && bytes.equal(query.name_at(tree, a), query.name_at(tree, b));", "return true;"),
    ("the note about an in is given for two types", "explainor.cho", "if query.cond_at(tree, j, 1) != query.cond_at(tree, k, 1) {\n        return false;", "if false {\n        return false;"),
    ("the note about an in is given for alternatives that leave differently", "explainor.cho", "|| query.flow_at(tree, j, 0) != query.flow_at(tree, k, 0) {", "{"),
    ("the note about an in lists the first value only", "explainor.cho", "written = written + 1;\n                    v = v + 1;", "written = written + 1;\n                    v = v + 100;"),
    ("a note is made for a run of one", "explainor.cho", "if starts && length >= 2 && values <= 1024 {", "if starts && length >= 1 && values <= 1024 {"),
    # ---- the query form (qparse.cho, qtrans.cho, qrepair.cho)
    ("the query's ceiling of parentheses is 9", "qparse.cho", "if depth >= 8 {\n            return qarena.fail(m, 1, 29, at);\n        }\n        step(m);", "if depth >= 9 {\n            return qarena.fail(m, 1, 29, at);\n        }\n        step(m);"),
    ("the query's mix is not noticed", "qparse.cho", "} else if kind != connective && m[qarena.s_mix()] == 0 {", "} else if false {"),
    ("the query's mix is noticed at its end", "qparse.cho", "m[qarena.s_mix()] = here + 1;", "m[qarena.s_mix()] = start_of(m, 0) + 1;"),
    ("the query's group is not closed", "qparse.cho", "if expect_sym(m, qlex.y_close(), 8) != 0 {\n            return 1;\n        }\n        structure(m, 0 - 2, close);", "structure(m, 0 - 2, close);"),
    ("a unit does not count its conditions at its start (a group or a not is the 65th unit)", "qparse.cho", "if m[qarena.s_cond_count()] >= 64 {\n        return qarena.fail(m, 1, 27, at);\n    }\n    if is_word(q, m, 0, \"not\") {", "if false {\n        return qarena.fail(m, 1, 27, at);\n    }\n    if is_word(q, m, 0, \"not\") {"),
    ("an or after the where clause is still the feature or", "qparse.cho", "if qarena.tok_kind(m, i) == qlex.t_id() && in_list(q, a, qarena.tok_end(m, i), \"union intersect except\") {", "if qarena.tok_kind(m, i) == qlex.t_id() && lower_eq(q, a, qarena.tok_end(m, i), \"or\") {\n            return qarena.fail(m, 2, 71, a);\n        }\n        if qarena.tok_kind(m, i) == qlex.t_id() && in_list(q, a, qarena.tok_end(m, i), \"union intersect except\") {"),
    ("an or is translated as an and", "qtrans.cho", "text = buffer.append(heap, text, \" or \");", "text = buffer.append(heap, text, \" and \");"),
    ("an opening parenthesis is not translated", "qtrans.cho", "if col == 0 - 1 {\n                text = buffer.push(heap, text, byte_of('('));", "if col == 0 - 1 {\n                text = buffer.push(heap, text, byte_of(' '));"),
    ("a closing parenthesis is not translated", "qtrans.cho", "} else if col == 0 - 2 {\n                text = buffer.push(heap, text, byte_of(')'));", "} else if col == 0 - 2 {\n                text = buffer.push(heap, text, byte_of(' '));"),
    ("a condition's values are written after a structure record's width", "qtrans.cho", "at = at + 8;\n        } else {", "at = at + 9;\n        } else {"),
    ("the mix does not come before the rest of the statement", "qtrans.cho", "if m[qarena.s_mix()] > 0 {", "if m[qarena.s_mix()] > 0 && bad == 0 && code == 0 {"),
    ("the mix repair is never offered", "qrepair.cho", "if kind == 5 {\n        return mixed_repair(", "if kind == 50 {\n        return mixed_repair("),
    ("the mix repair offers the first reading only", "qrepair.cho", "if ok1 {\n            borrow r1 as &rr in {", "if false {\n            borrow r1 as &rr in {"),
    ("the mix repair keeps the query's own where", "qrepair.cho", "if own_where {\n                    o = fail.choose_arg(heap, o, where_text);", "if false {\n                    o = fail.choose_arg(heap, o, where_text);"),
    ("the mix repair is offered for a rest that does not translate", "qrepair.cho", "if qtrans.info_at(t, qtrans.i_tolerant()) != 1 ||", "if false ||"),
]

# ---- slice A2: `not` (docs/or-and-columns.md section 17)
A2_MUTANTS = [
    # the grammar of --where: where `not` is a keyword
    ("not before in is the keyword (a column called not)", "orparse.cho", "!(bytes.equal(buffer.bytes(wr), \"in\") || bytes.equal(buffer.bytes(wr), \"contains\")", "!(bytes.equal(buffer.bytes(wr), \"i\") || bytes.equal(buffer.bytes(wr), \"contains\")"),
    ("not before contains is the keyword (a column called not)", "orparse.cho", "|| bytes.equal(buffer.bytes(wr), \"contains\") || bytes.equal(buffer.bytes(wr), \"not\"));", "|| bytes.equal(buffer.bytes(wr), \"c\") || bytes.equal(buffer.bytes(wr), \"not\"));"),
    ("not before not is the keyword (not not in)", "orparse.cho", "|| bytes.equal(buffer.bytes(wr), \"not\"));", "|| bytes.equal(buffer.bytes(wr), \"n\"));"),
    ("not before a quoted operator word is a column", "orparse.cho", "refuse = quoted2 == 1 || !(", "refuse = !("),
    ("not ( is not a unit", "orparse.cho", "if c == '(' {\n        return (1, j);", "if c == '(' {\n        return (0, 0);"),
    ("a quoted not is the keyword", "orparse.cho", "is_not = status == 0 && quoted == 0 && bytes.equal(buffer.bytes(wr), \"not\");", "is_not = status == 0 && bytes.equal(buffer.bytes(wr), \"not\");"),
    ("not at the end is refused as a word after not", "orparse.cho", "let j = expr.skip_space(src, after);\n    if j >= len(src) {\n        return (0, 0);", "let j = expr.skip_space(src, after);\n    if j >= len(src) {\n        return (2, j);"),
    ("the refusal of not COND is at the not", "orparse.cho", "what = 23;\n            at = refused_at;", "what = 23;\n            at = i;"),
    ("the refusal of not COND says the other words", "orparse.cho", "what = 23;\n            at = refused_at;", "what = 4;\n            at = refused_at;"),
    ("the ceiling of parentheses is not checked at the parenthesis of a not", "orparse.cho", "what = 21;\n                at = paren;", "what = 21;\n                at = i;"),
    ("a not group is a plain group (its exits are not swapped)", "orparse.cho", "paren = np;\n                group_kind = 6;", "paren = np;\n                group_kind = 3;"),
    ("a plan with a not group has no program unless it has an or", "orparse.cho", "flowed = o2 || group_kind == 6;", "flowed = o2;"),
    ("a plan with a not in has no program unless it has an or", "orparse.cho", "leaf_kind = 5;\n                    flowed = true;", "leaf_kind = 5;"),
    ("a negated condition is compiled as a plain one", "orparse.cho", "var leaf_kind = 0;\n                if negated {\n                    leaf_kind = 5;", "var leaf_kind = 0;\n                if false {\n                    leaf_kind = 5;"),
    ("the exits of a negated condition are not swapped", "orparse.cho", "// `not in`, `not contains`: the condition's own exits, swapped\n        let leaf = vec.get(tr, node + 1);\n        vec.set(flow, 2 * leaf, f);\n        vec.set(flow, 2 * leaf + 1, t);", "// `not in`, `not contains`: the condition's own exits, swapped\n        let leaf = vec.get(tr, node + 1);\n        vec.set(flow, 2 * leaf, t);\n        vec.set(flow, 2 * leaf + 1, f);"),
    ("the exits of a not group are not swapped", "orparse.cho", "return emit(tr, flow, vec.get(tr, node + 1), f, t);", "return emit(tr, flow, vec.get(tr, node + 1), t, f);"),
    ("the exits of a not group are doubly swapped (a not inside a not is lost)", "orparse.cho", "return emit(tr, flow, vec.get(tr, node + 1), f, t);", "return emit(tr, flow, vec.get(tr, node + 1), 0 - 2, 0 - 1);"),
    ("the first condition of a negated condition is not its own", "orparse.cho", "if kind == 0 || kind == 5 {\n        return vec.get(tr, node + 1);\n    }\n    if kind == 3 || kind == 6 {", "if kind == 0 {\n        return vec.get(tr, node + 1);\n    }\n    if kind == 3 || kind == 6 {"),
    ("the first condition of a not group is not its child's", "orparse.cho", "if kind == 3 || kind == 6 {\n        return first_leaf(", "if kind == 3 {\n        return first_leaf("),
    ("a reading writes a not group as a plain group", "orparse.cho", "o = buffer.append(heap, o, \"not (\");", "o = buffer.append(heap, o, \"(\");"),
    ("a reading writes a negated condition as its own text but without its not", "orparse.cho", "if kind == 0 || kind == 5 {\n        return buffer.append(heap, o, src[vec.get(tr, node + 2)..vec.get(tr, node + 3)]);", "if kind == 0 {\n        return buffer.append(heap, o, src[vec.get(tr, node + 2)..vec.get(tr, node + 3)]);"),
    # a condition (expr.cho)
    ("not contains is allowed on a typed column", "expr.cho", "if not_contains && typed != 0 {", "if false {"),
    ("not contains on a typed column is refused at the not", "expr.cho", "return (0, 0, at, 6, at, false);", "return (0, 0, from, 6, from, false);"),
    ("not in is the plain in", "expr.cho", "return (2, 0, after2, 0, 0, true);", "return (2, 0, after2, 0, 0, false);"),
    ("not contains is the plain contains", "expr.cho", "return (1, 0, after2, 0, 0, true);", "return (1, 0, after2, 0, 0, false);"),
    ("a not before neither in nor contains is refused with the words of the operator", "expr.cho", "return (0, 0, at, 24, at, false);", "return (0, 0, at, 2, at, false);"),
    ("a not before neither in nor contains is refused at the not", "expr.cho", "return (0, 0, at, 24, at, false);", "return (0, 0, from, 24, from, false);"),
    # the repair of not COND
    ("the complement of = is =", "expr.cho", "if c == '=' {\n        instead = \"!=\";", "if c == '=' {\n        instead = \"=\";"),
    ("the complement of != is !=", "expr.cho", "} else if c == '!' {\n        instead = \"=\";", "} else if c == '!' {\n        instead = \"!=\";"),
    ("the complement of < is >", "expr.cho", "} else if c == '<' {\n            instead = \">=\";", "} else if c == '<' {\n            instead = \">\";"),
    ("the complement of <= is >=", "expr.cho", "if c == '<' && or_equal {\n            instead = \">\";", "if c == '<' && or_equal {\n            instead = \">=\";"),
    ("the complement of > is >", "expr.cho", "} else {\n            instead = \"<=\";", "} else {\n            instead = \">\";"),
    ("the complement of >= is <=", "expr.cho", "} else if or_equal {\n            instead = \"<\";", "} else if or_equal {\n            instead = \"<=\";"),
    ("the complement of in is in", "expr.cho", "} else if first_in {\n            instead = \"not in\";", "} else if first_in {\n            instead = \"in\";"),
    ("the complement of contains is contains", "expr.cho", "} else {\n            instead = \"not contains\";", "} else {\n            instead = \"contains\";"),
    ("the complement of not in is not in", "expr.cho", "if second_in {\n                instead = \"in\";", "if second_in {\n                instead = \"not in\";"),
    ("the complement of not contains is not contains", "expr.cho", "} else {\n                instead = \"contains\";\n            }\n        } else if first_in {", "} else {\n                instead = \"not contains\";\n            }\n        } else if first_in {"),
    ("the complement drops the rest of the condition", "expr.cho", "out = buffer.append(heap, out, src[op_end..end]);", "out = buffer.append(heap, out, src[end..end]);"),
    ("the complement drops the column", "expr.cho", "var out = buffer.append(heap, buffer.empty(heap, end - from + 16), src[from..at]);", "var out = buffer.append(heap, buffer.empty(heap, end - from + 16), src[at..at]);"),
    ("the repair rewrites the expression once and offers it", "wrepair.cho", "while state == 0 && rounds < 70 {", "while state == 0 && rounds < 1 {"),
    ("the repair is offered though it is not whole", "wrepair.cho", "if w == 0 {\n                state = 1;\n            } else if w != 23 {\n                state = 2;", "if w == 0 {\n                state = 1;\n            } else if w != 23 {\n                state = 1;"),
    ("the repair does not look back over the space before the word", "wrepair.cho", "while k > 0 && (int_of(text[k - 1]) == 32 ||", "while k > 0 && false && (int_of(text[k - 1]) == 32 ||"),
    ("the repair takes an escaped not for the word", "wrepair.cho", " || k >= 4 && int_of(text[k - 4]) == '\\\\' {", " {"),
    ("the repair keeps the not", "wrepair.cho", "next = buffer.append(heap, next, text[0..k - 3]);", "next = buffer.append(heap, next, text[0..k]);"),
    ("the repair is offered on the wrong argument", "wrepair.cho", "o = option(heap, o, args, where_at, whole, fixed);", "o = option(heap, o, args, where_at + 1, whole, fixed);"),
    ("the repair mistakes the form of the flag", "wrepair.cho", "o = option(heap, o, args, where_at, whole, fixed);", "o = option(heap, o, args, where_at, !whole, fixed);"),
    ("the repair of not COND is never offered", "table.cho", "if what == 23 && where_at >= 0 {", "if what == 2300 && where_at >= 0 {"),
    # --explain
    ("the note about an in is given for a run that leaves by reject", "explainor.cho", "var starts = single(tree, k) && query.flow_at(tree, k, 0) != 0 - 2;", "var starts = single(tree, k);"),
    # the query form
    ("the query's not is not written", "qtrans.cho", "text = buffer.append(heap, text, \"not \");", "text = buffer.append(heap, text, \"\");"),
    ("the query's not in is written as in", "qtrans.cho", "text = buffer.append(heap, text, \"not in (\");", "text = buffer.append(heap, text, \"in (\");"),
    ("the query's not contains is written as contains", "qtrans.cho", "text = buffer.append(heap, text, \"not contains \");", "text = buffer.append(heap, text, \"contains \");"),
    ("the query's not in is not closed", "qtrans.cho", "if op == 7 || op == 8 {", "if op == 7 {"),
    ("the query's not in is the operator in", "qparse.cho", "m[r + 2] = 8;", "m[r + 2] = 7;"),
    ("the query's not contains is the operator contains", "qparse.cho", "contains_value(q, m, r, t, word, 9)", "contains_value(q, m, r, t, word, 6)"),
    ("the query's typed not contains is accepted", "qparse.cho", "if t != 0 {\n        return qarena.fail(m, 1, 15, qarena.tok_start(m, word));", "if false {\n        return qarena.fail(m, 1, 15, qarena.tok_start(m, word));"),
    ("the query's typed not contains is refused at the not", "qparse.cho", "contains_value(q, m, r, t, word, 9)", "contains_value(q, m, r, t, here, 9)"),
    ("the query's not like is the feature between", "qparse.cho", "return qarena.fail(m, 2, 73, qarena.tok_start(m, word));", "return qarena.fail(m, 2, 74, qarena.tok_start(m, word));"),
    ("the query's not = is refused at the not", "qparse.cho", "return qarena.fail(m, 1, 30, qarena.tok_start(m, word));", "return qarena.fail(m, 1, 30, qarena.tok_start(m, here));"),
    ("the query's not = says the words of the operator", "qparse.cho", "return qarena.fail(m, 1, 30, qarena.tok_start(m, word));", "return qarena.fail(m, 1, 13, qarena.tok_start(m, word));"),
    ("the query's not before a condition is not parenthesised", "qparse.cho", "structure(m, 0 - 1, at);\n        if unit(q, m, depth + 1) != 0 {\n            return 1;\n        }\n        structure(m, 0 - 2, start_of(m, 0));", "if unit(q, m, depth + 1) != 0 {\n            return 1;\n        }"),
    ("the query's not before a condition is parenthesised twice", "qparse.cho", "if is_sym(m, 0, qlex.y_open()) {\n            return unit(q, m, depth);\n        }\n        if depth >= 8 {", "if depth >= 8 {"),
    ("the query's not before a condition does not count a level", "qparse.cho", "if unit(q, m, depth + 1) != 0 {\n            return 1;\n        }\n        structure(m, 0 - 2, start_of(m, 0));", "if unit(q, m, depth) != 0 {\n            return 1;\n        }\n        structure(m, 0 - 2, start_of(m, 0));"),
    ("the query's ceiling for a not before a condition is 9", "qparse.cho", "if depth >= 8 {\n            return qarena.fail(m, 1, 29, at);\n        }\n        structure(m, 0 - 1, at);", "if depth >= 9 {\n            return qarena.fail(m, 1, 29, at);\n        }\n        structure(m, 0 - 1, at);"),
    ("the query's ceiling for a not before a condition is refused at the condition", "qparse.cho", "if depth >= 8 {\n            return qarena.fail(m, 1, 29, at);\n        }\n        structure(m, 0 - 1, at);", "if depth >= 8 {\n            return qarena.fail(m, 1, 29, start_of(m, 0));\n        }\n        structure(m, 0 - 1, at);"),
    ("the query's not is a keyword in any spelling but its own (a quoted name)", "qparse.cho", "if is_word(q, m, 0, \"not\") {\n        step(m);\n        structure(m, 0 - 5, at);", "if is_word(q, m, 0, \"nota\") {\n        step(m);\n        structure(m, 0 - 5, at);"),
]

A0_MUTANTS = [
    # ---- the flag form: what may be merged
    ("or_in: != is merged into the in", "expr.cho", "if w2 != 0 || kind == 1 || kind == 0 && op != 0 || negated {", "if w2 != 0 || kind == 1 || negated {"),
    ("or_in: contains is merged into the in", "expr.cho", "if w2 != 0 || kind == 1 || kind == 0 && op != 0 || negated {", "if w2 != 0 || kind == 0 && op != 0 || negated {"),
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
UNREACHED = "since slice A1 an or is not refused, so no refusal of the binary reaches the code of the repair of slice A0 (docs/or-and-columns.md section 7 keeps it for any other client that builds an expression); the mutant cannot be seen from outside, and the code is the pull request's to delete if the maintainer wants it gone"

EQUIVALENT = [e + (UNREACHED,) for e in A0_MUTANTS] + [
    ("or_in: a literal is read untyped by `parse_value` before the corrected expression is parsed", "expr.cho",
     "let (p3, after_value, w3, a3) = parse_value(heap, plan, src, i, typed);\n                    plan = p3;\n                    if w3 != 0 {\n                        ok = false;\n                    } else {\n                        if values > 0 {",
     "let (p3, after_value, w3, a3) = parse_value(heap, plan, src, i, 0);\n                    plan = p3;\n                    if w3 != 0 {\n                        ok = false;\n                    } else {\n                        if values > 0 {",
     "the corrected expression is parsed again by `parse`, with the column's type, and that is where a literal the type does not take is refused (the tests kill the mutant that skips that parse); the first reading only finds where each value ends, which the type does not change"),
    ("or_chain: one condition is a chain", "qparse.cho", "if leaves < 2 || is_word(q, m, 0, \"and\") {", "if leaves < 1 || is_word(q, m, 0, \"and\") {",
     "`or_chain` is asked only for a query refused at an `or` after a condition, so the clause has two conditions by the time its first `or` is met, and a clause of one condition is never asked"),
    ("the query's ceiling of conditions is 65", 'qparse.cho', 'if m[qarena.s_cond_count()] >= 64 {\n        return qarena.fail(m, 1, 27, at);\n    }\n    let (c, t) = typed', 'if m[qarena.s_cond_count()] >= 65 {\n        return qarena.fail(m, 1, 27, at);\n    }\n    let (c, t) = typed', '`unit` tests the count of conditions before it calls `condition`, and `condition` is called from `unit` only, so the test inside `condition` (kept from slice A1, where a group had a test of its own beside it) can never be the one that fires; the mutants of the test in `unit` are killed'),
    ('not before = is the keyword (a column called not)', 'orparse.cho', "if c == '=' || c == '!' || c == '<' || c == '>' {\n        return (0, 0);\n    }", 'if false {\n        return (0, 0);\n    }', 'after `not`, a word that starts with `=`, `!`, `<` or `>` is not a word at all (`read_word` answers status 1 for a delimiter), so `refuse` stays false and the answer is the same; the branch says it in one line and the mutant that removes it cannot change an answer'),
    ('the conjunction reader of A0 takes a negated condition', 'expr.cho', 'if what == 0 && negative {', 'if what == 0 && false {', '`expr.parse` (the reader of `COND { and COND }` of slice A0) is called only by `expr.or_in` on its own output (`COLUMN in (...)`, never negated) and by the repair of a query that A1 made unreachable (docs/or-and-columns.md section 16 note 8): no input of the binary reaches it with a negated condition'),
]

if __name__ == "__main__":
    # the equivalent mutants are not run, but their sites must still be there (a stale entry is a lie about what was proved)
    stale = mutlib.check([e[:4] for e in EQUIVALENT])
    for p in stale:
        print("!! (equivalent) " + p)
    if stale:
        sys.exit(1)
    sys.exit(mutlib.main(MUTANTS + A2_MUTANTS, TESTS))
