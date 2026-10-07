edition 5;

module expr;

// `--where`: conditions joined by `and`, and what each says of a row.
//
//     EXPR  := COND { "and" COND }
//     COND  := COLUMN OP VALUE | COLUMN "contains" VALUE
//            | COLUMN "in" "(" VALUE { "," VALUE } ")"
//     OP    := "=" | "!=" | "<" | "<=" | ">" | ">="
//     COLUMN:= WORD [":int" | ":dec(" N ")"]     a name, or #N the Nth column; N is a scale, 0 to 18
//     VALUE := WORD
//     WORD  := bare | 'quoted'
//
// A bare word runs to whitespace or one of ' ( ) , = ! < >; a backslash makes
// the next byte part of it, whatever it is (`\#3` is the name "#3", not the
// third column). A quoted word is between single quotes with '' for a quote;
// a quoted word is always a name or a value, never a position, and never has
// the keyword meaning of and, in or contains. `and`, `in` and `contains` are
// lower case. Nothing else is in the language: no or, no parentheses for
// grouping, no expressions, no functions.
//
// A condition compares text, bytewise, unless the column is written `:int`, in
// which case the cell is an exact integer and the literals are too, or `:dec(S)`, in
// which case the cell is an exact decimal of at most S fractional digits (docs/numbers.md:
// never rounded, `1.5` and `1.50` are one value) and so are the literals. Evaluation
// is left to right and stops at the first condition that is false, so a
// condition that would refuse a cell (an empty one is not an integer) is only
// reached by rows that passed those before it.

import std.buffer;
import std.bytes;
import query;

fn is_delimiter(c: int) -> [] bool {
    return c == 32 || c == 9 || c == 10 || c == 13 || c == 39 || c == '(' || c == ')' || c == ',' || c == '=' || c == '!' || c == '<' || c == '>';
}

fn skip_space[&s](src: &s [byte], from: int) -> [] int {
    var i = from;
    while i < len(src) && (int_of(src[i]) == 32 || int_of(src[i]) == 9 || int_of(src[i]) == 10 || int_of(src[i]) == 13) {
        i = i + 1;
    }
    return i;
}

// The word at `from`, appended to `out`. Answers (out, where it ends, quoted,
// status, whether its first byte was escaped, how many bytes up to and
// including the last escaped one). Status: 0 a word, 1 none here, 9 a quote
// never closed, 10 a backslash at the end.
fn read_word[&h, &s](heap: &!h Heap, out: buffer.Buffer, src: &s [byte], from: int) -> [heap] (buffer.Buffer, int, int, int, int, int) {
    var o = out;
    var j = from;
    var made = 0;
    var first_escaped = 0;
    var last_escaped = 0;
    if from < len(src) && int_of(src[from]) == 39 {
        j = from + 1;
        var closed = false;
        while !closed {
            let found = index_of_byte(src[j..len(src)], byte_of(39));
            if found < 0 {
                return (o, len(src), 1, 9, 0, 0);
            }
            o = buffer.append(heap, o, src[j..j + found]);
            if j + found + 1 < len(src) && int_of(src[j + found + 1]) == 39 {
                o = buffer.push(heap, o, byte_of(39));
                j = j + found + 2;
            } else {
                j = j + found + 1;
                closed = true;
            }
        }
        return (o, j, 1, 0, 0, 0);
    }
    while j < len(src) && !is_delimiter(int_of(src[j])) {
        if int_of(src[j]) == '\\' {
            if j + 1 >= len(src) {
                return (o, j, 0, 10, 0, 0);
            }
            if made == 0 {
                first_escaped = 1;
            }
            o = buffer.push(heap, o, src[j + 1]);
            made = made + 1;
            last_escaped = made;
            j = j + 2;
        } else {
            o = buffer.push(heap, o, src[j]);
            made = made + 1;
            j = j + 1;
        }
    }
    if made == 0 {
        return (o, j, 0, 1, 0, 0);
    }
    return (o, j, 0, 0, first_escaped, last_escaped);
}

// What was expected where the expression went wrong, by code.
pub fn expected(what: int) -> [] &static [byte] {
    if what == 1 {
        return "a column: a name, 'a quoted name' or #N";
    }
    if what == 2 {
        return "an operator: = != < <= > >= contains in";
    }
    if what == 3 {
        return "a value";
    }
    if what == 4 {
        return "and, or the end of the expression";
    }
    if what == 5 {
        return "an integer, because the column is :int";
    }
    if what == 6 {
        return "= != < <= > >= or in: contains compares text, and this column is :int or :dec";
    }
    if what == 7 {
        return "( after in";
    }
    if what == 8 {
        return ", or ) in a list of values";
    }
    if what == 9 {
        return "a closing quote";
    }
    if what == 10 {
        return "a byte after the backslash";
    }
    if what == 11 {
        return "fewer conditions or values: the ceiling is 64 conditions and 1024 values";
    }
    if what == 12 {
        return "an integer that fits 64 bits, because the column is :int";
    }
    if what == 13 {
        return "a decimal number: digits and at most one point, no exponent, no spaces, because the column is :dec";
    }
    if what == 14 {
        return "a decimal with no more fractional digits than the column's scale (write :dec(N) with a larger N to compare finer values)";
    }
    if what == 15 {
        return "a decimal below 10^18 once scaled to the column's scale (a smaller scale, or fewer digits)";
    }
    if what == 16 {
        return "a scale in parentheses after :dec, as :dec(2)";
    }
    if what == 17 {
        return "a scale from 0 to 18";
    }
    return "a complete expression";
}

// The scale of a `:dec(N)` suffix: `p` is where the `(` must be. Answers the type (2 + N), where the suffix ends, and what / where
// when it is not whole (16 a scale in parentheses was expected, 17 the scale is not 0 to 18).
fn dec_suffix[&s](src: &s [byte], p: int) -> [] (int, int, int, int) {
    if p >= len(src) || int_of(src[p]) != '(' {
        return (0, p, 16, p);
    }
    var i = p + 1;
    var value = 0;
    var digits = 0;
    while i < len(src) && int_of(src[i]) >= '0' && int_of(src[i]) <= '9' && digits < 3 {
        value = value * 10 + int_of(src[i]) - '0';
        digits = digits + 1;
        i = i + 1;
    }
    if digits == 0 {
        return (0, p, 16, p + 1);
    }
    if digits > 2 || value > 18 {
        return (0, p, 17, p + 1);
    }
    if i >= len(src) || int_of(src[i]) != ')' {
        return (0, p, 16, i);
    }
    return (2 + value, i + 1, 0, 0);
}

// A column at `from`: added to the plan as a name or a position. Answers the plan, where it ends, the name's index, its type (0 text,
// 1 :int, 2 + S for :dec(S)), and what / where when it is not a column.
fn parse_column[&h, &s](heap: &!h Heap, q: query.Query, src: &s [byte], from: int) -> [heap] (query.Query, int, int, int, int, int) {
    var plan = q;
    let (word, after, quoted, status, escaped_first, escaped_last) = read_word(heap, buffer.empty(heap, 16), src, from);
    var next = after;
    var typed = 0;
    var index = 0;
    var what = 0;
    var at = from;
    if status == 1 && quoted == 0 {
        what = 1;
    } else if status != 0 {
        what = status;
    } else {
        var length = 0;
        var is_dec = false;
        borrow word as &wr in {
            length = buffer.size(wr);
        }
        if quoted == 1 {
            if after + 4 <= len(src) && bytes.equal(src[after..after + 4], ":int") && (after + 4 == len(src) || is_delimiter(int_of(src[after + 4]))) {
                typed = 1;
                next = after + 4;
            } else if after + 4 <= len(src) && bytes.equal(src[after..after + 4], ":dec") {
                is_dec = true;
                next = after + 4;
            }
        } else if length >= 4 && escaped_last <= length - 4 {
            borrow word as &wr in {
                if bytes.equal(buffer.bytes(wr)[length - 4..length], ":int") {
                    typed = 1;
                    length = length - 4;
                } else if bytes.equal(buffer.bytes(wr)[length - 4..length], ":dec") {
                    is_dec = true;
                    length = length - 4;
                }
            }
        }
        if is_dec {
            let (t, nx, w, a) = dec_suffix(src, next);
            typed = t;
            next = nx;
            what = w;
            at = a;
        }
        if what == 0 {
            borrow word as &wr in {
                var position = -1;
                if quoted == 0 && escaped_first == 0 {
                    position = query.position_of(buffer.bytes(wr)[0..length]);
                }
                let (p2, ix) = query.add_name(heap, plan, buffer.bytes(wr)[0..length], position);
                plan = p2;
                index = ix;
            }
        }
    }
    buffer.drop(heap, word);
    return (plan, next, index, typed, what, at);
}

// The operator at `from`: answers its kind (0 compare, 1 contains, 2 in), its
// code, where it ends, and what / where on an error.
fn parse_operator[&h, &s](heap: &!h Heap, src: &s [byte], from: int, typed: int) -> [heap] (int, int, int, int, int) {
    if from >= len(src) {
        return (0, 0, from, 2, from);
    }
    let c = int_of(src[from]);
    if c == '=' {
        return (0, 0, from + 1, 0, 0);
    }
    if c == '!' {
        if from + 1 < len(src) && int_of(src[from + 1]) == '=' {
            return (0, 1, from + 2, 0, 0);
        }
        return (0, 0, from, 2, from);
    }
    if c == '<' || c == '>' {
        var base = 2;
        if c == '>' {
            base = 4;
        }
        if from + 1 < len(src) && int_of(src[from + 1]) == '=' {
            return (0, base + 1, from + 2, 0, 0);
        }
        return (0, base, from + 1, 0, 0);
    }
    let (word, after, quoted, status, e1, e2) = read_word(heap, buffer.empty(heap, 16), src, from);
    var is_contains = false;
    var is_in = false;
    borrow word as &wr in {
        is_contains = status == 0 && quoted == 0 && bytes.equal(buffer.bytes(wr), "contains");
        is_in = status == 0 && quoted == 0 && bytes.equal(buffer.bytes(wr), "in");
    }
    buffer.drop(heap, word);
    if is_contains && typed != 0 {
        return (0, 0, from, 6, from);
    }
    if is_contains {
        return (1, 0, after, 0, 0);
    }
    if is_in {
        return (2, 0, after, 0, 0);
    }
    return (0, 0, from, 2, from);
}

// One literal at `from`, added to the plan. Answers the plan, where it ends,
// and what / where on an error.
fn parse_value[&h, &s](heap: &!h Heap, q: query.Query, src: &s [byte], from: int, typed: int) -> [heap] (query.Query, int, int, int) {
    var plan = q;
    let (word, after, quoted, status, e1, e2) = read_word(heap, buffer.empty(heap, 16), src, from);
    var what = 0;
    if status == 1 && quoted == 0 {
        what = 3;
    } else if status != 0 {
        what = status;
    } else {
        var value = 0;
        if typed == 1 {
            borrow word as &wr in {
                let (v, bad) = query.parse_int(buffer.bytes(wr));
                value = v;
                if bad == 1 {
                    what = 5;
                } else if bad == 2 {
                    what = 12;
                }
            }
        } else if typed >= 2 {
            borrow word as &wr in {
                let (v, bad) = query.parse_dec(buffer.bytes(wr), typed - 2);
                value = v;
                if bad == 1 {
                    what = 13;
                } else if bad == 3 {
                    what = 14;
                } else if bad == 2 {
                    what = 15;
                }
            }
        }
        if what == 0 {
            borrow word as &wr in {
                plan = query.add_lit(heap, plan, buffer.bytes(wr), value);
            }
        }
    }
    buffer.drop(heap, word);
    return (plan, after, what, from);
}

// Read EXPR into the plan. Answers the plan, and 0 for what and the offset
// when it is whole, else what was expected (`expected`) and where, as a byte
// offset into the expression.
pub fn parse[&h, &s](heap: &!h Heap, q: query.Query, src: &s [byte]) -> [heap] (query.Query, int, int) {
    var plan = q;
    var i = skip_space(src, 0);
    var what = 0;
    var at = 0;
    var many = 0;
    var more = true;
    while more && what == 0 {
        let begins = i;
        if many >= 64 {
            what = 11;
            at = i;
        } else {
            let (p1, after_column, index, typed, w1, a1) = parse_column(heap, plan, src, i);
            plan = p1;
            what = w1;
            at = a1;
            if what == 0 {
                i = skip_space(src, after_column);
                let (kind, op, after_op, w2, a2) = parse_operator(heap, src, i, typed);
                what = w2;
                at = a2;
                i = after_op;
                var first = 0;
                borrow plan as &pr in {
                    first = query.lit_count(pr);
                }
                var values = 0;
                if what == 0 && kind == 2 {
                    i = skip_space(src, i);
                    if i < len(src) && int_of(src[i]) == '(' {
                        i = i + 1;
                        var listing = true;
                        while listing && what == 0 {
                            i = skip_space(src, i);
                            let (p3, after_value, w3, a3) = parse_value(heap, plan, src, i, typed);
                            plan = p3;
                            what = w3;
                            at = a3;
                            if what == 0 {
                                values = values + 1;
                                i = skip_space(src, after_value);
                                if i < len(src) && int_of(src[i]) == ',' {
                                    i = i + 1;
                                } else if i < len(src) && int_of(src[i]) == ')' {
                                    i = i + 1;
                                    listing = false;
                                } else {
                                    what = 8;
                                    at = i;
                                }
                                if values > 1024 {
                                    what = 11;
                                    at = i;
                                }
                            }
                        }
                    } else {
                        what = 7;
                        at = i;
                    }
                } else if what == 0 {
                    i = skip_space(src, i);
                    let (p3, after_value, w3, a3) = parse_value(heap, plan, src, i, typed);
                    plan = p3;
                    what = w3;
                    at = a3;
                    values = 1;
                    i = after_value;
                }
                if what == 0 {
                    plan = query.add_cond(heap, plan, kind, typed, op, index, first, values, begins);
                    many = many + 1;
                    i = skip_space(src, i);
                    if i >= len(src) {
                        more = false;
                    } else {
                        let (word, after_and, quoted, status, e1, e2) = read_word(heap, buffer.empty(heap, 4), src, i);
                        var is_and = false;
                        borrow word as &wr in {
                            is_and = status == 0 && quoted == 0 && bytes.equal(buffer.bytes(wr), "and");
                        }
                        buffer.drop(heap, word);
                        if is_and {
                            i = skip_space(src, after_and);
                        } else {
                            what = 4;
                            at = i;
                        }
                    }
                }
            }
        }
    }
    plan = query.bump(plan, 1, many);
    return (plan, what, at);
}

// The same for a :dec(scale) column: the cell is read as the exact scaled integer, and compared with the literals' (read at the
// same scale when the expression was parsed).
fn holds_dec[&q, &c](plan: &q query.Query, kind: int, op: int, first: int, many: int, scale: int, cell: &c [byte]) -> [] int {
    let (v, bad) = query.parse_dec(cell, scale);
    if bad == 1 {
        return 4;
    }
    if bad == 3 {
        return 5;
    }
    if bad == 2 {
        return 6;
    }
    var l = 0;
    while l < many {
        let w = query.lit_at(plan, first + l, 2);
        var yes = false;
        if kind == 2 || op == 0 {
            yes = v == w;
        } else if op == 1 {
            yes = v != w;
        } else if op == 2 {
            yes = v < w;
        } else if op == 3 {
            yes = v <= w;
        } else if op == 4 {
            yes = v > w;
        } else {
            yes = v >= w;
        }
        if yes {
            return 1;
        }
        l = l + 1;
    }
    return 0;
}

// Whether `cell` satisfies condition `k`: 1 yes, 0 no, 2 the column is :int
// and the cell is not an integer, 3 it is one that does not fit 64 bits; for a
// :dec column 4 the cell is not a decimal, 5 it has more fractional digits than
// the scale, 6 it is too wide (`query.parse_dec`).
fn holds[&q, &c](plan: &q query.Query, k: int, cell: &c [byte]) -> [] int {
    let kind = query.cond_at(plan, k, 0);
    let op = query.cond_at(plan, k, 2);
    let first = query.cond_at(plan, k, 4);
    let many = query.cond_at(plan, k, 5);
    let typed = query.cond_at(plan, k, 1);
    if typed >= 2 {
        return holds_dec(plan, kind, op, first, many, typed - 2, cell);
    }
    if typed == 1 {
        let (v, bad) = query.parse_int(cell);
        if bad == 1 {
            return 2;
        }
        if bad == 2 {
            return 3;
        }
        var l = 0;
        while l < many {
            let w = query.lit_at(plan, first + l, 2);
            var yes = false;
            if kind == 2 || op == 0 {
                yes = v == w;
            } else if op == 1 {
                yes = v != w;
            } else if op == 2 {
                yes = v < w;
            } else if op == 3 {
                yes = v <= w;
            } else if op == 4 {
                yes = v > w;
            } else {
                yes = v >= w;
            }
            if yes {
                return 1;
            }
            l = l + 1;
        }
        return 0;
    }
    if kind == 1 {
        let needle = query.lit_bytes(plan, first);
        if len(needle) == 0 || bytes.find(cell, needle) >= 0 {
            return 1;
        }
        return 0;
    }
    var l = 0;
    while l < many {
        let c = bytes.compare(cell, query.lit_bytes(plan, first + l));
        var yes = false;
        if kind == 2 || op == 0 {
            yes = c == 0;
        } else if op == 1 {
            yes = c != 0;
        } else if op == 2 {
            yes = c < 0;
        } else if op == 3 {
            yes = c <= 0;
        } else if op == 4 {
            yes = c > 0;
        } else {
            yes = c >= 0;
        }
        if yes {
            return 1;
        }
        l = l + 1;
    }
    return 0;
}

// Whether the record satisfies every condition. `cols` are the resolved
// column numbers of the plan's names and `cells` the record's fields (see
// `reader.fields`). Answers (verdict, which condition, scratch): verdict 1
// the row is wanted, 0 it is not, 2 or 3 a condition refused a cell (see
// `holds`).
pub fn eval[&h, &q, &c, &d, &e](heap: &!h Heap, plan: &q query.Query, cols: &c [int], record: &d [byte], cells: &e [int], scratch: buffer.Buffer) -> [heap] (int, int, buffer.Buffer) {
    var scr = scratch;
    var k = 0;
    let total = query.count_of(plan, 1);
    while k < total {
        let column = cols[query.cond_at(plan, k, 3)];
        let first = cells[3 * column];
        let last = cells[3 * column + 1];
        var verdict = 0;
        if cells[3 * column + 2] == 1 && index_of_byte(record[first..last], byte_of(34)) >= 0 {
            borrow mut scr as &!sw in {
                buffer.clear(sw);
            }
            scr = query.unquote(heap, scr, record[first..last]);
            borrow scr as &sr in {
                verdict = holds(plan, k, buffer.bytes(sr));
            }
        } else {
            verdict = holds(plan, k, record[first..last]);
        }
        if verdict != 1 {
            return (verdict, k, scr);
        }
        k = k + 1;
    }
    return (1, 0, scr);
}
