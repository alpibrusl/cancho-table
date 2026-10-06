edition 5;

module query;

// The plan: what a call asks of the table, in one structure, whichever flags
// said it. `--where`, `--group`, `--agg` and `--select` fill a `Query`; the
// read evaluates it; a later `--query` string would compile to the same one
// (docs/filter.md).
//
// Every column the plan names, by name or position, is one entry of `names`
// (its bytes, where each ends, and its position or -1), in the order the flags
// were read: select's, then where's (one per condition), then group's, then
// aggregates' (one per aggregate that has a column). After the header is read
// they are resolved together to column numbers, and the rest of the plan
// refers to a name by its index in that list.
//
// `conds` has 7 integers per condition:
//     kind (0 compare, 1 contains, 2 in), int (the column is :int),
//     op (0 =, 1 !=, 2 <, 3 <=, 4 >, 5 >=), name (index into names),
//     first literal and how many (into `lits`), offset (in the expression).
// `lits` has 4 per literal: where its bytes begin and end in `ltext`, its
// integer value when the condition is :int, 0.
// `aggs` has 2 per aggregate: function (0 count, 1 sum, 2 min, 3 max,
// 4 distinct) and name (or -1 for count).
// `meta` is: names for select, conditions, names for group, aggregates.

import std.buffer;
import std.bytes;
import std.vec;

pub res struct Query {
    names: buffer.Buffer,
    nends: vec.Vec[int],
    nkinds: vec.Vec[int],
    conds: vec.Vec[int],
    lits: vec.Vec[int],
    ltext: buffer.Buffer,
    aggs: vec.Vec[int],
    meta: vec.Vec[int],
}

pub fn empty[&h](heap: &!h Heap) -> [heap] Query {
    var meta = vec.empty(heap, 4, 0);
    meta = vec.push(heap, meta, 0);
    meta = vec.push(heap, meta, 0);
    meta = vec.push(heap, meta, 0);
    meta = vec.push(heap, meta, 0);
    return Query { names: buffer.empty(heap, 64), nends: vec.empty(heap, 8, 0), nkinds: vec.empty(heap, 8, 0), conds: vec.empty(heap, 8, 0), lits: vec.empty(heap, 8, 0), ltext: buffer.empty(heap, 64), aggs: vec.empty(heap, 4, 0), meta: meta };
}

pub fn drop[&h](heap: &!h Heap, q: Query) -> [heap] int {
    let Query { names, nends, nkinds, conds, lits, ltext, aggs, meta } = q;
    buffer.drop(heap, names);
    vec.drop(heap, nends);
    vec.drop(heap, nkinds);
    vec.drop(heap, conds);
    vec.drop(heap, lits);
    buffer.drop(heap, ltext);
    vec.drop(heap, aggs);
    vec.drop(heap, meta);
    return 0;
}

// One more column reference: its bytes and its position (-1 for a name).
// Answers its index.
pub fn add_name[&h, &d](heap: &!h Heap, q: Query, given: &d [byte], position: int) -> [heap] (Query, int) {
    let Query { names, nends, nkinds, conds, lits, ltext, aggs, meta } = q;
    var at = 0;
    borrow nends as &r in {
        at = vec.size(r);
    }
    var n = buffer.append(heap, names, given);
    var size = 0;
    borrow n as &r in {
        size = buffer.size(r);
    }
    return (Query { names: n, nends: vec.push(heap, nends, size), nkinds: vec.push(heap, nkinds, position), conds: conds, lits: lits, ltext: ltext, aggs: aggs, meta: meta }, at);
}

// meta[slot] += by (slot: 0 select, 1 conditions, 2 group, 3 aggregates).
pub fn bump(q: Query, slot: int, by: int) -> [] Query {
    let Query { names, nends, nkinds, conds, lits, ltext, aggs, meta } = q;
    var m = meta;
    borrow mut m as &!w in {
        vec.set(w, slot, vec.get(w, slot) + by);
    }
    return Query { names: names, nends: nends, nkinds: nkinds, conds: conds, lits: lits, ltext: ltext, aggs: aggs, meta: m };
}

pub fn count_of[&q](q: &q Query, slot: int) -> [] int {
    return vec.get(q.meta, slot);
}

pub fn name_count[&q](q: &q Query) -> [] int {
    return vec.size(q.nends);
}

// Name `k`'s bytes.
pub fn name_at[&q](q: &q Query, k: int) -> [] &q [byte] {
    var begin = 0;
    if k > 0 {
        begin = vec.get(q.nends, k - 1);
    }
    return buffer.bytes(q.names)[begin..vec.get(q.nends, k)];
}

pub fn kind_at[&q](q: &q Query, k: int) -> [] int {
    return vec.get(q.nkinds, k);
}

// The largest and smallest `int`, written so that neither is a literal the
// lexer must take whole.
pub fn int_max() -> [] int {
    return 9223372036854775807;
}

pub fn int_min() -> [] int {
    return 0 - 9223372036854775807 - 1;
}

// `data` as an exact integer: an optional sign, then at least one digit and
// nothing else. Answers (value, status): 0 it is, 1 it is not an integer
// (empty, a sign alone, a space, a point, an exponent, a letter), 2 it is
// one that does not fit 64 bits. Never a float, never a wrap.
pub fn parse_int[&d](data: &d [byte]) -> [] (int, int) {
    var at = 0;
    var negative = false;
    if len(data) > 0 && (int_of(data[0]) == '-' || int_of(data[0]) == '+') {
        negative = int_of(data[0]) == '-';
        at = 1;
    }
    if at >= len(data) {
        return (0, 1);
    }
    if len(data) - at <= 18 {
        // At most 18 digits cannot leave 64 bits: no check on the way, and a positive accumulator.
        var plain = 0;
        while at < len(data) {
            let c = int_of(data[at]);
            if c < '0' || c > '9' {
                return (0, 1);
            }
            plain = plain * 10 + c - '0';
            at = at + 1;
        }
        if negative {
            return (0 - plain, 0);
        }
        return (plain, 0);
    }
    // Accumulated as a negative number, which has room for -2^63.
    var acc = 0;
    var over = false;
    while at < len(data) {
        let c = int_of(data[at]);
        if c < '0' || c > '9' {
            return (0, 1);
        }
        let digit = c - '0';
        if acc < (int_min() + digit) / 10 {
            over = true;
        } else if !over {
            acc = acc * 10 - digit;
        }
        at = at + 1;
    }
    if over {
        return (0, 2);
    }
    if negative {
        return (acc, 0);
    }
    if acc == int_min() {
        return (0, 2);
    }
    return (0 - acc, 0);
}

// a + b, or ok = false when it does not fit.
pub fn add_checked(a: int, b: int) -> [] (int, bool) {
    if b > 0 && a > int_max() - b {
        return (0, false);
    }
    if b < 0 && a < int_min() - b {
        return (0, false);
    }
    return (a + b, true);
}

// One more literal: its bytes (kept in `ltext`) and its integer value.
pub fn add_lit[&h, &d](heap: &!h Heap, q: Query, given: &d [byte], value: int) -> [heap] Query {
    let Query { names, nends, nkinds, conds, lits, ltext, aggs, meta } = q;
    var t = ltext;
    var begin = 0;
    borrow t as &r in {
        begin = buffer.size(r);
    }
    t = buffer.append(heap, t, given);
    var l = vec.push(heap, lits, begin);
    l = vec.push(heap, l, begin + len(given));
    l = vec.push(heap, l, value);
    l = vec.push(heap, l, 0);
    return Query { names: names, nends: nends, nkinds: nkinds, conds: conds, lits: l, ltext: t, aggs: aggs, meta: meta };
}

pub fn lit_count[&q](q: &q Query) -> [] int {
    return vec.size(q.lits) / 4;
}

// One more condition (the 7 integers above).
pub fn add_cond[&h](heap: &!h Heap, q: Query, kind: int, as_int: int, op: int, name: int, first: int, many: int, offset: int) -> [heap] Query {
    let Query { names, nends, nkinds, conds, lits, ltext, aggs, meta } = q;
    var c = vec.push(heap, conds, kind);
    c = vec.push(heap, c, as_int);
    c = vec.push(heap, c, op);
    c = vec.push(heap, c, name);
    c = vec.push(heap, c, first);
    c = vec.push(heap, c, many);
    c = vec.push(heap, c, offset);
    return Query { names: names, nends: nends, nkinds: nkinds, conds: c, lits: lits, ltext: ltext, aggs: aggs, meta: meta };
}

// One more aggregate: function code and name index (-1 for count).
pub fn add_agg[&h](heap: &!h Heap, q: Query, function: int, name: int) -> [heap] Query {
    let Query { names, nends, nkinds, conds, lits, ltext, aggs, meta } = q;
    var a = vec.push(heap, aggs, function);
    a = vec.push(heap, a, name);
    return Query { names: names, nends: nends, nkinds: nkinds, conds: conds, lits: lits, ltext: ltext, aggs: a, meta: meta };
}

pub fn cond_at[&q](q: &q Query, k: int, field: int) -> [] int {
    return vec.get(q.conds, 7 * k + field);
}

pub fn lit_at[&q](q: &q Query, l: int, field: int) -> [] int {
    return vec.get(q.lits, 4 * l + field);
}

pub fn lit_bytes[&q](q: &q Query, l: int) -> [] &q [byte] {
    return buffer.bytes(q.ltext)[vec.get(q.lits, 4 * l)..vec.get(q.lits, 4 * l + 1)];
}

pub fn agg_count[&q](q: &q Query) -> [] int {
    return vec.size(q.aggs) / 2;
}

pub fn agg_at[&q](q: &q Query, k: int, field: int) -> [] int {
    return vec.get(q.aggs, 2 * k + field);
}

// The bytes of a quoted field with its doubled quotes undone, appended to `out`.
pub fn unquote[&h, &d](heap: &!h Heap, out: buffer.Buffer, data: &d [byte]) -> [heap] buffer.Buffer {
    var o = out;
    var p = 0;
    while p < len(data) {
        let found = index_of_byte(data[p..len(data)], byte_of(34));
        if found < 0 {
            o = buffer.append(heap, o, data[p..len(data)]);
            p = len(data);
        } else {
            o = buffer.append(heap, o, data[p..p + found + 1]);
            p = p + found + 2;
        }
    }
    return o;
}

// The position `#N` names in a word, or -1: `#` and one to nine digits.
pub fn position_of[&w](word: &w [byte]) -> [] int {
    if len(word) < 2 || len(word) > 10 || int_of(word[0]) != '#' {
        return -1;
    }
    var n = 0;
    var i = 1;
    while i < len(word) {
        if !bytes.is_digit(int_of(word[i])) {
            return -1;
        }
        n = n * 10 + bytes.digit_of(int_of(word[i]));
        i = i + 1;
    }
    return n;
}

fn copy_vec[&h, &v](heap: &!h Heap, from: &v vec.Vec[int]) -> [heap] vec.Vec[int] {
    var out = vec.empty(heap, vec.size(from) + 1, 0);
    var i = 0;
    while i < vec.size(from) {
        out = vec.push(heap, out, vec.get(from, i));
        i = i + 1;
    }
    return out;
}

fn copy_buffer[&h, &b](heap: &!h Heap, from: &b buffer.Buffer) -> [heap] buffer.Buffer {
    return buffer.append(heap, buffer.empty(heap, buffer.size(from) + 1), buffer.bytes(from));
}

// The plan again, in memory of its own: a worker thread gets one, because the plan is
// read by all of them and owned by none.
pub fn duplicate[&h, &q](heap: &!h Heap, plan: &q Query) -> [heap] Query {
    return Query { names: copy_buffer(heap, plan.names), nends: copy_vec(heap, plan.nends), nkinds: copy_vec(heap, plan.nkinds), conds: copy_vec(heap, plan.conds), lits: copy_vec(heap, plan.lits), ltext: copy_buffer(heap, plan.ltext), aggs: copy_vec(heap, plan.aggs), meta: copy_vec(heap, plan.meta) };
}
