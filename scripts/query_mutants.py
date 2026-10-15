#!/usr/bin/env python3
"""Mutation check of the gates of `--query` (tests/conformance/test_query.py, scripts/query_corpus.py; docs/query.md sections 8 and 11, Q1-MUT).

Each mutant is the front end broken in one way (the lexer, the parser, the checks of the translation, one writing of a name for one flag, the source, the conflicts, the repairs,
the plan that the translation fills), rebuilt and run against the tests. A mutant is killed when a test fails or the build refuses it; one that survives is a hole in the gates, or an
equivalent mutant, which is then listed in EQUIVALENT with the reason it changes nothing.

    python3 scripts/query_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_query", "test_inputs"]

MUTANTS = [
    # ---- the lexer
    ("lexer: a doubled quote ends the string", "qlex.cho", "if at(q, j + 1) == quote {", "if at(q, j + 1) == 0 - 7 {"),
    ("lexer: the sign of a number is not read", "qlex.cho", "if at(q, j) == '+' || at(q, j) == '-' {", "if at(q, j) == '+' {"),
    ("lexer: an exponent is not read", "qlex.cho", "if at(q, j) == 'e' || at(q, j) == 'E' {", "if at(q, j) == 'e' {"),
    ("lexer: the sign of an exponent is not read", "qlex.cho", "if at(q, k) == '+' || at(q, k) == '-' {", "if at(q, k) == '+' {"),
    ("lexer: a name has no digits", "qlex.cho", "while j < size && (is_letter(int_of(q[j])) || is_digit(int_of(q[j]))) {", "while j < size && is_letter(int_of(q[j])) {"),
    ("lexer: a string token ends one byte early", "qlex.cho", "n = put(m, n, t_str(), i, j + 1, 0);", "n = put(m, n, t_str(), i, j, 0);"),
    ("lexer: a quoted name token starts one byte late", "qlex.cho", "n = put(m, n, t_qid(), i, j + 1, 0);", "n = put(m, n, t_qid(), i + 1, j + 1, 0);"),
    ("lexer: a position needs no digits", "qlex.cho", "if j == i + 1 {\n                return qarena.fail(m, 1, 24, i);", "if j == i - 1 {\n                return qarena.fail(m, 1, 24, i);"),
    ("lexer: 4096 bytes are too long", "qtrans.cho", "if len(q) > largest() {", "if len(q) >= largest() {"),
    ("lexer: 4097 bytes are not too long", "qtrans.cho", "pub fn largest() -> [] int {\n    return 4096;", "pub fn largest() -> [] int {\n    return 4097;"),
    ("lexer: -- is not a comment", "qlex.cho", "c == '-' && at(q, i + 1) == '-' ||", "c == '-' && at(q, i + 1) == '+' ||"),
    ("lexer: /* is not a comment", "qlex.cho", "c == '/' && at(q, i + 1) == '*' {", "c == '/' && at(q, i + 1) == '+' {"),
    ("lexer: <> is two symbols", "qlex.cho", "} else if c == '<' && at(q, i + 1) == '>' {", "} else if c == '<' && at(q, i + 1) == '<' {"),
    ("lexer: :: is two symbols", "qlex.cho", "} else if c == ':' && at(q, i + 1) == ':' {", "} else if c == ':' && at(q, i + 1) == ';' {"),
    ("lexer: % is a byte that starts nothing", "qlex.cho", "c == '|' || c == '+' || c == '/' || c == '%' || c == '-'", "c == '|' || c == '+' || c == '/' || c == '-'"),
    ("lexer: an empty quoted name is a name", "qlex.cho", "if j == i + 1 {\n                return qarena.fail(m, 2, 60, i);", "if j == i - 7 {\n                return qarena.fail(m, 2, 60, i);"),
    ("lexer: the end token is at the wrong place", "qlex.cho", "n = put(m, n, t_end(), size, size, 0);", "n = put(m, n, t_end(), size - 1, size, 0);"),
    # ---- keywords
    ("parser: keywords are case-sensitive", "qparse.cho", "if bytes.to_lower(int_of(q[start + i])) != int_of(word[i]) {", "if int_of(q[start + i]) != int_of(word[i]) {"),
    ("parser: cast is not reserved", "qparse.cho", "return \"select from where group order limit offset by and in contains asc desc as cast\";", "return \"select from where group order limit offset by and in contains asc desc as\";"),
    ("parser: exists is not reserved", "qparse.cho", " over with all any exists\";", " over with all any\";"),
    ("parser: join is not reserved", "qparse.cho", "return \"join inner left", "return \"inner left"),
    ("parser: a keyword is a column when quoted only for some", "qparse.cho", "if keyword(q, m, i) {", "if keyword(q, m, i) && qarena.tok_kind(m, i) == qlex.t_id() && qarena.tok_end(m, i) - qarena.tok_start(m, i) != 3 {"),
    # ---- the parser
        ("parser: and ends the conditions", "qparse.cho", "if is_word(q, m, 0, \"and\") {\n            connective = 1;", "if is_word(q, m, 0, \"and\") {\n            connective = 0;"),
        ("parser: an in list has one value", "qparse.cho", "listing = eat_sym(m, qlex.y_comma());\n        }\n        if expect_sym(m, qlex.y_close(), 20)", "listing = false;\n        }\n        if expect_sym(m, qlex.y_close(), 20)"),
    ("parser: :: is not a cast", "qparse.cho", "if eat_sym(m, qlex.y_cast()) {", "if eat_sym(m, qlex.y_cast() + 90) {"),
    ("parser: cast(c as t) reads the type of the wrong word", "qparse.cho", "if !eat_word(q, m, \"as\") {", "if !eat_word(q, m, \"as\") && !is_word(q, m, 0, \"int\") {"),
    ("parser: count(distinct c) is not distinct", "qparse.cho", "} else if eat_word(q, m, \"distinct\") {", "} else if eat_word(q, m, \"distinct!\") {"),
    ("parser: a trailing ; is an error", "qparse.cho", "eat_sym(m, qlex.y_semi());", "eat_sym(m, qlex.y_semi() + 90);"),
    ("parser: two statements are one", "qparse.cho", "if qarena.tok_kind(m, i) != qlex.t_end() {", "if qarena.tok_kind(m, i) != qlex.t_end() && qarena.tok_kind(m, i) != qlex.t_id() {"),
        ("parser: the 65th condition is accepted", "qparse.cho", "if m[qarena.s_cond_count()] >= 64 {\n        return qarena.fail(m, 1, 27, at);\n    }\n    let (c, t) = typed", "if m[qarena.s_cond_count()] >= 65 {\n        return qarena.fail(m, 1, 27, at);\n    }\n    let (c, t) = typed"),
        ("parser: the 64th condition is refused", "qparse.cho", "if m[qarena.s_cond_count()] >= 64 {\n        return qarena.fail(m, 1, 27, at);\n    }\n    let (c, t) = typed", "if m[qarena.s_cond_count()] >= 63 {\n        return qarena.fail(m, 1, 27, at);\n    }\n    let (c, t) = typed"),
    ("parser: the 1024th value is refused", "qparse.cho", "if values >= 1024 {", "if values >= 1023 {"),
    ("parser: a limit of 19 digits is accepted", "qparse.cho", "if b - a > 18 {", "if b - a > 19 {"),
    ("parser: a scale of 19 is accepted", "qparse.cho", "if most >= 0 && value > most {", "if most >= 0 && value > most + 1 {"),
    ("parser: sum is min", "qparse.cho", "if is_word(q, m, 0, \"sum\") {\n        f = 1;", "if is_word(q, m, 0, \"sum\") {\n        f = 2;"),
    ("parser: mean has no scale", "qparse.cho", "if f == 5 && eat_sym(m, qlex.y_comma()) {", "if f == 7 && eat_sym(m, qlex.y_comma()) {"),
    ("parser: an alias is not refused", "qparse.cho", "if is_word(q, m, 0, \"as\") {\n            return qarena.fail(m, 2, 65, start_of(m, 0));", "if is_word(q, m, 0, \"asx\") {\n            return qarena.fail(m, 2, 65, start_of(m, 0));"),
    ("parser: a column comparison is a value", "qparse.cho", "if nk == qlex.t_qid() || nk == qlex.t_pos() {", "if nk == qlex.t_pos() {"),
        ("parser: contains takes a typed column", "qparse.cho", "if t != 0 {\n            return qarena.fail(m, 1, 15,", "if t == 77 {\n            return qarena.fail(m, 1, 15,"),
    ("parser: offset alone is an error", "qparse.cho", "} else if eat_word(q, m, \"offset\") {", "} else if eat_word(q, m, \"offsetx\") {"),
    ("parser: from stdin is a file", "qparse.cho", "} else if qarena.tok_kind(m, i) == qlex.t_id() && lower_eq(q, a, b, \"stdin\") {", "} else if qarena.tok_kind(m, i) == qlex.t_id() && lower_eq(q, a, b, \"stdinx\") {"),
    ("parser: a typed group by column is accepted", "qparse.cho", "if t != 0 {\n            return qarena.fail(m, 2, 64, at);", "if t == 77 {\n            return qarena.fail(m, 2, 64, at);"),
    ("parser: a typed select item is accepted", "qparse.cho", "if t != 0 {\n                return qarena.fail(m, 2, 63, at);", "if t == 77 {\n                return qarena.fail(m, 2, 63, at);"),
    ("parser: asc is not read", "qparse.cho", "eat_word(q, m, \"asc\");", "eat_word(q, m, \"ascx\");"),
    # ---- the checks of the translation
    ("check: a grouping may select *", "qtrans.cho", "        if star {\n            qarena.fail(m, 3, 40, items_at);", "        if star && n_items == 77 {\n            qarena.fail(m, 3, 40, items_at);"),
    ("check: the aggregates may come first", "qtrans.cho", "var same = cols_first && n_cols == n_groups;", "var same = n_cols == n_groups;"),
    ("check: the columns may be fewer than the groups", "qtrans.cho", "var same = cols_first && n_cols == n_groups;", "var same = cols_first;"),
    ("check: the columns may be other than the groups", "qtrans.cho", "same = same_column(heap, q, m, m[items + 8 * g + 2], m[groups + g]);", "same = true;"),
    ("check: a grouping may have no aggregate", "qtrans.cho", "if n_aggs == 0 {\n            qarena.fail(m, 3, 42, items_at);", "if n_aggs == 77 {\n            qarena.fail(m, 3, 42, items_at);"),
    ("check: a position is the same column as a name", "qtrans.cho", "if is_position(m, x) != is_position(m, y) {", "if false {"),
    ("check: an order by of a grouping may be two keys", "qtrans.cho", "if n_orders != 1 {", "if n_orders == 77 {"),
    ("check: an order by may name an aggregate that is not selected", "qtrans.cho", "if !listed {\n                    qarena.fail(m, 3, 43, where_key);", "if !listed && n_items == 77 {\n                    qarena.fail(m, 3, 43, where_key);"),
    ("check: an order by of a grouping may name a column that is not grouped", "qtrans.cho", "if !listed {\n                    qarena.fail(m, 3, 44, where_key);", "if !listed && n_items == 77 {\n                    qarena.fail(m, 3, 44, where_key);"),
    ("check: a position is a sort label", "qtrans.cho", "if is_position(m, m[r + 2]) {\n                    qarena.fail(m, 2, 80, where_key);", "if is_position(m, m[r + 2]) && n_items == 77 {\n                    qarena.fail(m, 2, 80, where_key);"),
    ("check: a name that starts with - is a sort label", "qtrans.cho", "            if dash {\n", "            if dash && n_items == 77 {\n"),
    ("check: an aggregate may order rows", "qtrans.cho", "                if m[r] == 1 {\n                    qarena.fail(m, 3, 45, m[r + 6]);", "                if m[r] == 77 {\n                    qarena.fail(m, 3, 45, m[r + 6]);"),
    ("write: the type of an order key is lost", "qtrans.cho", "                order_text = put_type(heap, order_text, t);\n", ""),
    ("check: a count in the order of a grouping matches any aggregate", "qtrans.cho", "if m[x + 1] != m[y + 1] || m[x + 3] != m[y + 3] || m[x + 4] != m[y + 4] {", "if m[x + 1] != m[y + 1] {"),
    ("check: an aggregate matches on its function only", "qtrans.cho", "    return same_column(heap, q, m, m[x + 2], m[y + 2]);", "    return same_column(heap, q, m, m[x + 2], m[x + 2]);"),
    # ---- the writing of a name, one for each flag (docs/query.md section 3)
    ("write: a comma in a name is not escaped", "qtrans.cho", "if c == '\\\\' || c == ',' || (agg || order) && c == ':' || agg && c == '@' {", "if c == '\\\\' || (agg || order) && c == ':' || agg && c == '@' {"),
    ("write: a backslash in a name is not escaped", "qtrans.cho", "if c == '\\\\' || c == ',' || (agg || order) && c == ':' || agg && c == '@' {", "if c == ',' || (agg || order) && c == ':' || agg && c == '@' {"),
    ("write: a leading # in a name is not escaped", "qtrans.cho", "if len(name) > 0 && (int_of(name[0]) == '#' || order && int_of(name[0]) == '-') {", "if len(name) > 0 && (order && int_of(name[0]) == '-') {"),
    ("write: a leading - in an order key is not escaped", "qtrans.cho", "if len(name) > 0 && (int_of(name[0]) == '#' || order && int_of(name[0]) == '-') {", "if len(name) > 0 && (int_of(name[0]) == '#') {"),
    ("write: a colon in an order key is not escaped", "qtrans.cho", "if c == '\\\\' || c == ',' || (agg || order) && c == ':' || agg && c == '@' {", "if c == '\\\\' || c == ',' || agg && c == ':' || agg && c == '@' {"),
    ("write: a colon in an aggregate's column is not escaped", "qtrans.cho", "if c == '\\\\' || c == ',' || (agg || order) && c == ':' || agg && c == '@' {", "if c == '\\\\' || c == ',' || order && c == ':' || agg && c == '@' {"),
    ("write: an at-sign in an aggregate's column is not escaped", "qtrans.cho", "if c == '\\\\' || c == ',' || (agg || order) && c == ':' || agg && c == '@' {", "if c == '\\\\' || c == ',' || (agg || order) && c == ':' {"),
    ("write: a position is written as a name", "qtrans.cho", "    if position {\n        return buffer.append(heap, o, name);\n    }\n", ""),
    ("write: a quote in a name of --where is not doubled", "qtrans.cho", "                if int_of(n[i]) == 39 {\n                    o = buffer.push(heap, o, byte_of(39));", "                if int_of(n[i]) == 77 {\n                    o = buffer.push(heap, o, byte_of(39));"),
    ("write: every name of --where is bare", "qtrans.cho", "} else if is_identifier(n) && !bytes.equal(n, \"and\")", "} else if !bytes.equal(n, \"and\")"),
    ("write: the scale of dec is off by one", "qtrans.cho", "o = buffer.push_nat(heap, o, t - 2);", "o = buffer.push_nat(heap, o, t - 1);"),
    ("write: float is written int", "qtrans.cho", "o = buffer.append(heap, o, \":float\");", "o = buffer.append(heap, o, \":int\");"),
        ("write: the type of a condition is lost", "qtrans.cho", "            text = put_type(heap, text, t);\n            text = buffer.push(heap, text, byte_of(32));", "            text = buffer.push(heap, text, byte_of(32));"),
        ("write: the last condition is dropped", "qtrans.cho", "    while at < m[qarena.s_conds_end()] {", "    while at + 9 < m[qarena.s_conds_end()] {"),
    ("write: the last value of an in is dropped", "qtrans.cho", "        while v < nv {", "        while v < nv - 1 {"),
    ("write: contains is =", "qtrans.cho", "text = buffer.append(heap, text, \"contains \");", "text = buffer.append(heap, text, \"= \");"),
    ("write: <> is <>", "qparse.cho", "if m_aux == qlex.y_ne() || m_aux == qlex.y_ltgt() {", "if m_aux == qlex.y_ne() {"),
        ("write: < is <=", "qtrans.cho", "} else if op == 2 {\n                text = buffer.append(heap, text, \"< \");", "} else if op == 2 {\n                text = buffer.append(heap, text, \"<= \");"),
        ("write: >= is >", "qtrans.cho", "            } else {\n                text = buffer.append(heap, text, \">= \");", "            } else {\n                text = buffer.append(heap, text, \"> \");"),
        ("write: != is =", "qtrans.cho", "} else if op == 1 {\n                text = buffer.append(heap, text, \"!= \");", "} else if op == 1 {\n                text = buffer.append(heap, text, \"= \");"),
    ("write: the scale of mean is dropped", "qtrans.cho", "if f == 5 && m[r + 4] >= 0 {", "if f == 7 && m[r + 4] >= 0 {"),
    ("write: the group by is reversed", "qtrans.cho", "        text = put_column(heap, text, q, m, m[groups + i], false, false);", "        text = put_column(heap, text, q, m, m[groups + n_groups - 1 - i], false, false);"),
    ("write: the select list is reversed", "qtrans.cho", "            text = put_column(heap, text, q, m, m[items + 8 * s + 2], false, false);", "            text = put_column(heap, text, q, m, m[items + 8 * (n_cols - 1 - s) + 2], false, false);"),
    ("write: the sort key of a grouping is ascending", "qtrans.cho", "            if m[r + 5] == 1 {\n                var signed", "            if m[r + 5] == 77 {\n                var signed"),
    ("write: the order key is ascending", "qtrans.cho", "                if m[r + 5] == 1 {\n                    order_text = buffer.push(heap, order_text, byte_of('-'));", "                if m[r + 5] == 77 {\n                    order_text = buffer.push(heap, order_text, byte_of('-'));"),
    ("write: the aggregate list loses its second item", "qtrans.cho", "                if !first {\n                    text = buffer.push(heap, text, byte_of(','));\n                }\n                first = false;", "                first = false;"),
    ("write: a doubled quote in the source path stays", "qtrans.cho", "            if int_of(q[j]) == 39 {\n                j = j + 1;\n            }\n            j = j + 1;\n        }\n    }\n    text = put_zero(heap, text);\n    info = put_info(info, i_source()", "            j = j + 1;\n        }\n    }\n    text = put_zero(heap, text);\n    info = put_info(info, i_source()"),
    ("write: limit and offset are swapped", "inputs.cho", "from_n = qtrans.info_at(t, qtrans.i_from());", "from_n = qtrans.info_at(t, qtrans.i_limit());"),
    ("write: limit is off by one", "inputs.cho", "limit_n = qtrans.info_at(t, qtrans.i_limit());", "limit_n = qtrans.info_at(t, qtrans.i_limit()) + 1;"),
    ("write: the offset is lost", "inputs.cho", "    if qtrans.info_at(t, qtrans.i_from_given()) == 1 {\n        has_from = true;", "    if qtrans.info_at(t, qtrans.i_from_given()) == 7 {\n        has_from = true;"),
    ("write: --agg is not given to a grouping", "qtrans.cho", "        info = put_info(info, i_agg(), 1);", "        info = put_info(info, i_agg(), 0);"),
    ("write: a sort is not given to the plan", "table.cho", "    if inp.has_sort && inp.has_query {\n        tree = query.set_sort(heap, tree, sort_t);", "    if inp.has_sort && inp.has_query && len(sort_t) > 99999 {\n        tree = query.set_sort(heap, tree, sort_t);"),
    ("write: the plan's sort label is not read", "frame.cho", "    var sort = query.sort_label(tree);", "    var sort = sort_flag;"),
    ("write: the sort flag does not win over the plan's", "frame.cho", "    if len(sort_flag) > 0 {\n        sort = sort_flag;", "    if len(sort_flag) > 77 {\n        sort = sort_flag;"),
    ("write: select * alone is not --rows", "qtrans.cho", "if star && n_conds == 0 && n_orders == 0 && !grouped {", "if star && n_orders == 0 && !grouped {"),
    ("write: select * with an order is --rows", "qtrans.cho", "if star && n_conds == 0 && n_orders == 0 && !grouped {", "if star && n_conds == 0 && !grouped {"),
    # ---- the plan: --rows, the source, the conflicts
    ("plan: --rows is not rows", "table.cho", "|| has_report || inp.has_rows {\n        mode = 1;", "|| has_report {\n        mode = 1;"),
    ("plan: --rows takes a --where", "table.cho", "if inp.has_rows && (has_select || has_where || grouped || has_order || has_report) {", "if inp.has_rows && (has_select || grouped || has_order || has_report) {"),
    ("plan: --rows takes a --select", "table.cho", "if inp.has_rows && (has_select || has_where || grouped || has_order || has_report) {", "if inp.has_rows && (has_where || grouped || has_order || has_report) {"),
    ("plan: --rows takes a --group", "table.cho", "if inp.has_rows && (has_select || has_where || grouped || has_order || has_report) {", "if inp.has_rows && (has_select || has_where || has_order || has_report) {"),
    ("plan: --rows takes an --order-by", "table.cho", "if inp.has_rows && (has_select || has_where || grouped || has_order || has_report) {", "if inp.has_rows && (has_select || has_where || grouped || has_report) {"),
    ("plan: --rows takes a --report", "table.cho", "if inp.has_rows && (has_select || has_where || grouped || has_order || has_report) {", "if inp.has_rows && (has_select || has_where || grouped || has_order) {"),
    ("source: a query's from path is the operand's", "table.cho", "} else if inp.src_file {", "} else if inp.src_file && false {"),
    ("source: a path is not confined (dotdot)", "qpath.cho", "if path.has_dotdot(given) {", "if path.has_dotdot(given) && len(root) == 77 {"),
    ("source: a path is not confined (absolute)", "qpath.cho", "    if absolute {\n        var below", "    if absolute && len(root) == 77 {\n        var below"),
    ("source: an empty path is a path", "qpath.cho", "    if len(given) == 0 {\n        e = refuse(heap, e, \"path.empty\"", "    if len(given) == 77 {\n        e = refuse(heap, e, \"path.empty\""),
    ("conflict: --select does not conflict", "table.cho", "    if inp.has_select {\n        clash = buffer.append(heap, clash, \" --select\");", "    if inp.has_select && false {\n        clash = buffer.append(heap, clash, \" --select\");"),
    ("conflict: --where does not conflict", "table.cho", "    if inp.has_where {\n        clash", "    if inp.has_where && false {\n        clash"),
    ("conflict: --group does not conflict", "table.cho", "    if inp.has_group {\n        clash", "    if inp.has_group && false {\n        clash"),
    ("conflict: --agg does not conflict", "table.cho", "    if inp.has_agg {\n        clash", "    if inp.has_agg && false {\n        clash"),
    ("conflict: --sort does not conflict", "table.cho", "    if inp.has_sort {\n        clash", "    if inp.has_sort && false {\n        clash"),
    ("conflict: --order-by does not conflict", "table.cho", "    if inp.has_order {\n        clash", "    if inp.has_order && false {\n        clash"),
    ("conflict: --rows does not conflict", "table.cho", "    if inp.has_rows {\n        clash", "    if inp.has_rows && false {\n        clash"),
    ("conflict: --report does not conflict", "table.cho", "    if inp.has_report {\n        clash", "    if inp.has_report && false {\n        clash"),
    ("conflict: --limit beside a limit does not conflict", "table.cho", "if inp.has_limit && limit_given {", "if inp.has_limit && false {"),
    ("conflict: --from beside an offset does not conflict", "table.cho", "if inp.has_from && from_given {", "if inp.has_from && false {"),
    ("conflict: --top beside a limit does not conflict", "table.cho", "if inp.has_top && limit_given {", "if inp.has_top && false {"),
    ("conflict: --top beside a query conflicts", "table.cho", "if inp.has_top && limit_given {", "if inp.has_top {"),
    ("conflict: --limit beside a query without a limit conflicts", "table.cho", "if inp.has_limit && limit_given {", "if inp.has_limit {"),
    ("conflict: from and an operand do not conflict", "table.cho", "if source != 0 && inp.operands > 0 {", "if source == 77 && inp.operands > 0 {"),
    ("conflict: from stdin and an operand do not conflict", "table.cho", "if source != 0 && inp.operands > 0 {", "if source == 1 && inp.operands > 0 {"),
    ("conflict: a flag's limit is lost when the query has none", "inputs.cho", "    var limit_n = inp.limit_n;\n    var has_limit = inp.has_limit;", "    var limit_n = 0;\n    var has_limit = inp.has_limit;"),
    # ---- the literal that does not fit its column
    ("literal: refused at the first literal", "qrefuse.cho", "if qtrans.info_at(tr, qtrans.i_header() + 6 * k) <= at {", "if qtrans.info_at(tr, qtrans.i_header() + 6 * k) <= at && k == 0 {"),
    ("literal: refused before the literal", "qrefuse.cho", "if qtrans.info_at(tr, qtrans.i_header() + 6 * k) <= at {", "if qtrans.info_at(tr, qtrans.i_header() + 6 * k) < at {"),
    ("literal: the offset is the translation's", "qrefuse.cho", "            offset = lit_a;", "            offset = at;"),
    ("literal: the value is the column's", "qrefuse.cho", "w = fail.detail_text(heap, w, \"value\", q[lit_a..lit_b]);", "w = fail.detail_text(heap, w, \"value\", q[col_a..col_b]);"),
    # ---- the refusals
    ("refusal: the offset of a refusal is one late", "qrefuse.cho", "    w = fail.detail_int(heap, w, \"offset\", offset);\n    if kind == 2 {", "    w = fail.detail_int(heap, w, \"offset\", offset + 1);\n    if kind == 2 {"),
    ("refusal: a group-mismatch has no select", "qrefuse.cho", "w = fail.detail_text(heap, w, \"select\", bytes.trim(q[a..b]));", "w = fail.detail_text(heap, w, \"select\", q[0..0]);"),
    ("refusal: a group-mismatch has no group by", "qrefuse.cho", "if qtrans.info_at(t, qtrans.i_group_end()) > 0 {", "if qtrans.info_at(t, qtrans.i_group_end()) > 99999 {"),
    ("refusal: too-long says a wrong limit", "qrefuse.cho", "w = fail.detail_int(heap, w, \"limit\", qtrans.largest());", "w = fail.detail_int(heap, w, \"limit\", 4095);"),
    # ---- the repairs
    ("repair: a keyword typo of one edit is not repaired", "qrepair.cho", "    while d <= 2 {", "    while d <= 0 {"),
    ("repair: the query is not replaced where it was", "qrepair.cho", "        if i == flag_at {\n            if qtrans.info_at(t, qtrans.i_rows()) == 1 {", "        if i == 0 {\n            if qtrans.info_at(t, qtrans.i_rows()) == 1 {"),
    ("repair: the source is not the operand", "qrepair.cho", "    if qtrans.info_at(t, qtrans.i_source()) == 1 {\n        o = fail.choose_arg(heap, o, qtrans.section(t, 6));", "    if qtrans.info_at(t, qtrans.i_source()) == 7 {\n        o = fail.choose_arg(heap, o, qtrans.section(t, 6));"),
    ("repair: an operand the call has is repeated", "qrepair.cho", "&& !(qtrans.info_at(tr, qtrans.i_source()) == 1 && operands > 0)", ""),
    ("repair: == is not repaired", "qrepair.cho", "if kind == 1 && code == 14 {", "if kind == 1 && code == 77 {"),
    ("repair: integer is not int", "qrepair.cho", "if spelled(q, a, b, \"integer\") {", "if spelled(q, a, b, \"integerx\") {"),
    ("repair: decimal is not dec", "qrepair.cho", "if spelled(q, a, b, \"decimal\") {", "if spelled(q, a, b, \"decimalx\") {"),
    ("repair: double is not float", "qrepair.cho", "if spelled(q, a, b, \"double\") {", "if spelled(q, a, b, \"doublex\") {"),
    ("repair: a double-quoted value is not repaired", "qrepair.cho", "if kind == 2 && code == 68 && qarena.tok_kind(m, ti) == qlex.t_qid() {", "if kind == 2 && code == 77 && qarena.tok_kind(m, ti) == qlex.t_qid() {"),
    ("repair: like is repaired whatever the pattern", "qrepair.cho", "if int_of(q[j]) == '%' || int_of(q[j]) == '_' {", "if int_of(q[j]) == 77 {"),
    ("repair: like 'x%' is repaired", "qrepair.cho", "if sb - sa >= 5 && int_of(q[sa + 1]) == '%' && int_of(q[sb - 2]) == '%' {", "if sb - sa >= 5 && int_of(q[sa + 1]) == '%' {"),
    ("repair: a grouping loses its aggregates", "qrepair.cho", "        if m[items + 8 * i] == 1 {\n            clean = buffer.append(heap, clean, \", \");", "        if m[items + 8 * i] == 77 {\n            clean = buffer.append(heap, clean, \", \");"),
    ("repair: the order's aggregate is not added", "qrepair.cho", "    if code == 43 {\n        let r = m[qarena.s_orders()];", "    if code == 77 {\n        let r = m[qarena.s_orders()];"),
    ("repair: a grouping with no aggregate is not given count", "qrepair.cho", "    if aggs == 0 {\n        clean = buffer.append(heap, clean, \", count(*)\");", "    if aggs == 77 {\n        clean = buffer.append(heap, clean, \", count(*)\");"),
]

# A mutant that cannot be killed because it changes nothing, with the reason (docs/query.md section 14). The list is checked by --check as the rest is: its sites must still match.
EQUIVALENT = [
    ("parser: the last error is reported, not the first", "qarena.cho", "if m[s_code()] == 0 {", "if true {",
     "every production returns at the first failure it sets, so `qarena.fail` is never called a second time and keeping the last error is keeping the first (10,000 and 100,000 mutated strings agree with the reference on the offset of the first error)"),
    ("parser: the 1025th value is accepted", "qparse.cho", "if values >= 1024 {", "if values >= 1025 {",
     "the translation's ceiling and the engine's are the same number: when the first lets the 1,025th value through, `--where`'s parser refuses it with the same words, and `qrefuse.literal_refusal` turns that into the same `query.syntax` at the same offset (the last literal of the translation is the 1,025th), so the two checks are one refusal twice"),
    ("write: and is a bare name in --where", "qtrans.cho", "} else if is_identifier(n) && !bytes.equal(n, \"and\") && !bytes.equal(n, \"in\") && !bytes.equal(n, \"contains\") {", "} else if is_identifier(n) && !bytes.equal(n, \"in\") && !bytes.equal(n, \"contains\") {",
     "a bare `and` where a column stands is read as a name by `--where`'s parser (it is a word only between conditions), so the quotes the translation puts on it change nothing a call can see"),
    ("write: in is a bare name in --where", "qtrans.cho", "} else if is_identifier(n) && !bytes.equal(n, \"and\") && !bytes.equal(n, \"in\") && !bytes.equal(n, \"contains\") {", "} else if is_identifier(n) && !bytes.equal(n, \"and\") && !bytes.equal(n, \"contains\") {",
     "as for `and`: a bare `in` in the place of a column is a name to `--where`'s parser"),
    ("write: contains is a bare name in --where", "qtrans.cho", "} else if is_identifier(n) && !bytes.equal(n, \"and\") && !bytes.equal(n, \"in\") && !bytes.equal(n, \"contains\") {", "} else if is_identifier(n) && !bytes.equal(n, \"and\") && !bytes.equal(n, \"in\") {",
     "as for `and`: a bare `contains` in the place of a column is a name to `--where`'s parser"),
    ("write: a refused sort shows no label", "finish.cho", "    var shown = query.sort_label(tree);", "    var shown = flagged;",
     "a query cannot name a sort key that is not an output column (the translation refuses a position and a name that starts with a dash, and writes the label as the output prints it), so `sort.unknown-key` cannot be raised through a query and its detail is only the flag's"),
    ("source: from stdin is a file", "inputs.cho", "    if qtrans.info_at(t, qtrans.i_source()) == 2 {\n        stdin = true;", "    if qtrans.info_at(t, qtrans.i_source()) == 7 {\n        stdin = true;",
     "`from stdin` is refused beside an operand, so there is none: the standard input that `Inputs.stdin` already has when there is no operand is the same one"),
    ("source: a long path is a path", "qpath.cho", "if len(given) > path.longest() {", "if len(given) > 99999 {",
     "a path of over 4,096 bytes does not fit in a query of at most 4,096 (the check is the operand's, kept for the day the limits differ)"),
    ("source: the root is not joined", "qpath.cho", "    full = buffer.append(heap, full, root);", "    full = buffer.append(heap, full, given);",
     "with a root, `place.open_operand` opens the path as `shown` spells it beneath the root and reads `full` only for `.` (the root itself, a directory either way, refused as one with the same words), so the root in `full` is not observable; without a root `full` is the path itself, and that is tested"),
    ("repair: a keyword typo of three edits is repaired", "qrepair.cho", "    while d <= 2 {", "    while d <= 3 {",
     "`distance` answers 3 for 'more than two' and the loop's bound only changes whether it takes the third turn to learn it: every caller asks for at most 2"),
]

if __name__ == "__main__":
    # the equivalent mutants are not run, but their sites must still be there (a stale entry is a lie about what was proved)
    stale = mutlib.check([e[:4] for e in EQUIVALENT])
    for p in stale:
        print("!! (equivalent) " + p)
    if stale:
        sys.exit(1)
    sys.exit(mutlib.main(MUTANTS, TESTS))
