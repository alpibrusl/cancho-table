edition 5;

module agg;

// `--group` and `--agg`: rows counted and summed into groups, in bounded memory.
//
// A group is the tuple of its group columns' values, kept as bytes: for each
// column a 4-byte little-endian length and the value (a quoted field's doubled
// quotes undone). The key is the key of a `std.map`; the group's number is its
// entry number, so the order of insertion is the order of the numbers; the
// *output* order is not that: it is `--sort`, and ties and the default are the
// keys compared field by field, each bytewise (`bytes.compare`), a shorter
// value first. That order depends on the values only, not on where in the
// file a group first appeared, so it is the same for the same rows in any
// order.
//
// Each group has `1 + aggregates` integers: its row count, then one per
// aggregate: the sum, the minimum, the maximum, or the number of distinct
// values. Sum, min and max need every cell they see to be an exact integer
// (an empty cell is not: filter it out with --where first) and a sum that would
// leave 64 bits is refused, never wrapped. `distinct` counts bytes, and keeps
// every (group, value) pair it has seen, which is what `--max-distinct` bounds.
//
// Three bounds, each its own refusal: groups (`--max-groups`), distinct pairs
// (`--max-distinct`), and the bytes of every key held (`--max-state-bytes`),
// because a count of keys bounds memory only if the keys are short.

import std.buffer;
import std.bytes;
import std.map;
import std.vec;
import plan;
import query;
import toolbox.text;

pub res struct Groups {
    index: map.Map[int],
    acc: vec.Vec[int],
    seen: map.Map[int],
    keyb: buffer.Buffer,
    dkey: buffer.Buffer,
    held: int,
    pairs: int,
    stride: int,
}

pub fn start[&h](heap: &!h Heap, aggregates: int) -> [heap] Groups {
    return Groups { index: map.empty(heap, 64, 0, 0x5eed), acc: vec.empty(heap, 64, 0), seen: map.empty(heap, 4, 0, 0x5eed), keyb: buffer.empty(heap, 64), dkey: buffer.empty(heap, 64), held: 0, pairs: 0, stride: 1 + 2 * aggregates };
}

pub fn drop[&h](heap: &!h Heap, g: Groups) -> [heap] int {
    let Groups { index, acc, seen, keyb, dkey, held, pairs, stride } = g;
    map.drop(heap, index);
    vec.drop(heap, acc);
    map.drop(heap, seen);
    buffer.drop(heap, keyb);
    buffer.drop(heap, dkey);
    return 0;
}

pub fn groups[&g](g: &g Groups) -> [] int {
    return map.size(g.index);
}

fn put_length[&h](heap: &!h Heap, out: buffer.Buffer, n: int) -> [heap] buffer.Buffer {
    var o = buffer.push(heap, out, byte_of(n & 255));
    o = buffer.push(heap, o, byte_of(n >> 8 & 255));
    o = buffer.push(heap, o, byte_of(n >> 16 & 255));
    return buffer.push(heap, o, byte_of(n >> 24 & 255));
}

fn length_at[&k](key: &k [byte], at: int) -> [] int {
    return int_of(key[at]) | int_of(key[at + 1]) << 8 | int_of(key[at + 2]) << 16 | int_of(key[at + 3]) << 24;
}

// |v|, with the one value that has none (the minimum) answered as the maximum.
fn int_max_of(v: int) -> [] int {
    if v >= 0 {
        return v;
    }
    if v == query.int_min() {
        return query.int_max();
    }
    return 0 - v;
}

// A field of a record, as the bytes it holds, appended after its length.
fn put_cell[&h, &d](heap: &!h Heap, out: buffer.Buffer, scratch: buffer.Buffer, record: &d [byte], first: int, last: int, quoted: int) -> [heap] (buffer.Buffer, buffer.Buffer) {
    var scr = scratch;
    if quoted == 1 && index_of_byte(record[first..last], byte_of(34)) >= 0 {
        borrow mut scr as &!sw in {
            buffer.clear(sw);
        }
        scr = query.unquote(heap, scr, record[first..last]);
        var n = 0;
        var o = out;
        borrow scr as &sr in {
            n = buffer.size(sr);
        }
        o = put_length(heap, o, n);
        borrow scr as &sr in {
            o = buffer.append(heap, o, buffer.bytes(sr));
        }
        return (o, scr);
    }
    var o = put_length(heap, out, last - first);
    o = buffer.append(heap, o, record[first..last]);
    return (o, scr);
}

// A field as an integer: (value, 0 ok / 1 not an integer / 2 too large, scratch).
fn int_cell[&h, &d](heap: &!h Heap, scratch: buffer.Buffer, record: &d [byte], first: int, last: int, quoted: int) -> [heap] (int, int, buffer.Buffer) {
    var scr = scratch;
    if quoted == 1 && index_of_byte(record[first..last], byte_of(34)) >= 0 {
        borrow mut scr as &!sw in {
            buffer.clear(sw);
        }
        scr = query.unquote(heap, scr, record[first..last]);
        var v = 0;
        var bad = 0;
        borrow scr as &sr in {
            let (x, y) = query.parse_int(buffer.bytes(sr));
            v = x;
            bad = y;
        }
        return (v, bad, scr);
    }
    let (v, bad) = query.parse_int(record[first..last]);
    return (v, bad, scr);
}

fn set_at(acc: vec.Vec[int], at: int, value: int) -> [] vec.Vec[int] {
    var a = acc;
    borrow mut a as &!w in {
        vec.set(w, at, value);
    }
    return a;
}

// One more row into its group. Answers the groups, the scratch, a status and
// which aggregate it is about: 0 added; 1 more than `max_groups`; 2 more than
// `max_distinct` pairs; 3 more than `max_state` bytes of keys; 4 a cell that is
// not an integer; 5 an integer of more than 64 bits; 6 a sum past 64 bits.
pub fn add[&h, &q, &c, &d, &e](heap: &!h Heap, g: Groups, tree: &q query.Query, cols: &c [int], record: &d [byte], cells: &e [int], scratch: buffer.Buffer, max_groups: int, max_distinct: int, max_state: int, track: bool) -> [heap] (Groups, buffer.Buffer, int, int) {
    let Groups { index, acc, seen, keyb, dkey, held, pairs, stride } = g;
    var key = keyb;
    var scr = scratch;
    var m = index;
    var a = acc;
    var s = seen;
    var dk = dkey;
    var bytes_held = held;
    var distinct_pairs = pairs;
    borrow mut key as &!kw in {
        buffer.clear(kw);
    }
    let ng = query.count_of(tree, 2);
    let base = query.count_of(tree, 0) + query.count_of(tree, 1);
    var j = 0;
    while j < ng {
        let column = cols[base + j];
        let (k2, s2) = put_cell(heap, key, scr, record, cells[3 * column], cells[3 * column + 1], cells[3 * column + 2]);
        key = k2;
        scr = s2;
        j = j + 1;
    }
    var status = 0;
    var which = 0;
    var entry = -1;
    var size = 0;
    borrow key as &kr in {
        borrow m as &mr in {
            entry = map.find(mr, buffer.bytes(kr));
            size = map.size(mr);
        }
        if entry < 0 {
            if size >= max_groups {
                status = 1;
            } else if bytes_held + buffer.size(kr) > max_state {
                status = 3;
            } else {
                bytes_held = bytes_held + buffer.size(kr);
                m = map.put(heap, m, buffer.bytes(kr), 0);
                entry = size;
                var z = 0;
                while z < stride {
                    a = vec.push(heap, a, 0);
                    z = z + 1;
                }
            }
        }
    }
    if status == 0 {
        let at = entry * stride;
        var before = 0;
        borrow a as &ar in {
            before = vec.get(ar, at);
        }
        a = set_at(a, at, before + 1);
        let na = query.agg_count(tree);
        var k = 0;
        while k < na && status == 0 {
            let function = query.agg_at(tree, k, 0);
            if function != 0 {
                let column = cols[query.agg_at(tree, k, 1)];
                let first = cells[3 * column];
                let last = cells[3 * column + 1];
                let quoted = cells[3 * column + 2];
                var now = 0;
                borrow a as &ar in {
                    now = vec.get(ar, at + 1 + k);
                }
                if function == 4 {
                    // distinct: the pair (group, value) is new, or not.
                    borrow mut dk as &!dw in {
                        buffer.clear(dw);
                    }
                    dk = put_length(heap, dk, entry);
                    dk = buffer.push(heap, dk, byte_of(k));
                    let (d2, s2) = put_cell(heap, dk, scr, record, first, last, quoted);
                    dk = d2;
                    scr = s2;
                    var known = false;
                    borrow dk as &dr in {
                        borrow s as &sr in {
                            known = map.find(sr, buffer.bytes(dr)) >= 0;
                        }
                        if !known {
                            if distinct_pairs >= max_distinct {
                                status = 2;
                            } else if bytes_held + buffer.size(dr) > max_state {
                                status = 3;
                            } else {
                                bytes_held = bytes_held + buffer.size(dr);
                                distinct_pairs = distinct_pairs + 1;
                                s = map.put(heap, s, buffer.bytes(dr), 0);
                            }
                        }
                    }
                    if status == 0 && !known {
                        a = set_at(a, at + 1 + k, now + 1);
                    }
                } else {
                    let (v, bad, s3) = int_cell(heap, scr, record, first, last, quoted);
                    scr = s3;
                    if bad == 1 {
                        status = 4;
                    } else if bad == 2 {
                        status = 5;
                    } else if function == 1 {
                        let (sum, fits) = query.add_checked(now, v);
                        if fits {
                            a = set_at(a, at + 1 + k, sum);
                            if track {
                                // The largest the running sum has been in size: what lets the sum of
                                // several ranges be checked without reading them in order (par.ls).
                                var magnitude = int_max_of(sum);
                                var seen_peak = 0;
                                borrow a as &ar in {
                                    seen_peak = vec.get(ar, at + 1 + (stride - 1) / 2 + k);
                                }
                                if magnitude > seen_peak {
                                    a = set_at(a, at + 1 + (stride - 1) / 2 + k, magnitude);
                                }
                            }
                        } else {
                            status = 6;
                        }
                    } else if before == 0 {
                        a = set_at(a, at + 1 + k, v);
                    } else if function == 2 && v < now {
                        a = set_at(a, at + 1 + k, v);
                    } else if function == 3 && v > now {
                        a = set_at(a, at + 1 + k, v);
                    }
                }
                if status != 0 {
                    which = k;
                }
            }
            k = k + 1;
        }
    }
    return (Groups { index: m, acc: a, seen: s, keyb: key, dkey: dk, held: bytes_held, pairs: distinct_pairs, stride: stride }, scr, status, which);
}

// -1, 0 or 1: the keys of two groups, field by field, bytewise.
fn compare_keys[&a, &b](x: &a [byte], y: &b [byte], fields: int) -> [] int {
    var px = 0;
    var py = 0;
    var j = 0;
    while j < fields {
        let lx = length_at(x, px);
        let ly = length_at(y, py);
        let c = bytes.compare(x[px + 4..px + 4 + lx], y[py + 4..py + 4 + ly]);
        if c != 0 {
            if c < 0 {
                return 0 - 1;
            }
            return 1;
        }
        px = px + 4 + lx;
        py = py + 4 + ly;
        j = j + 1;
    }
    return 0;
}

// Whether group `x` is before group `y`: by group column `field` (or, when
// that is -1, by the integer at `slot` of the group's row) ascending or
// descending, then by key; neither given orders by key alone.
fn before[&m, &v, &r](index: &m map.Map[int], acc: &v vec.Vec[int], pre: &r [int], stride: int, fields: int, field: int, slot: int, descending: bool, x: int, y: int) -> [] bool {
    if slot >= 0 {
        let vx = vec.get(acc, x * stride + slot);
        let vy = vec.get(acc, y * stride + slot);
        if vx != vy {
            if descending {
                return vx > vy;
            }
            return vx < vy;
        }
    } else if field == 0 && pre[x] != pre[y] {
        // The first seven bytes of the first column already say.
        if descending {
            return pre[x] > pre[y];
        }
        return pre[x] < pre[y];
    } else if field >= 0 {
        let c = compare_field(map.key_at(index, x), map.key_at(index, y), field);
        if c != 0 {
            if descending {
                return c > 0;
            }
            return c < 0;
        }
    }
    if fields >= 1 && pre[x] != pre[y] {
        return pre[x] < pre[y];
    }
    return compare_keys(map.key_at(index, x), map.key_at(index, y), fields) < 0;
}

// The first seven bytes of a key's first column as one number, bytewise order kept
// (a shorter value is padded with zero bytes, and a tie is settled by the whole keys).
fn prefix_of[&k](key: &k [byte], fields: int) -> [] int {
    if fields < 1 {
        return 0;
    }
    let n = length_at(key, 0);
    var v = 0;
    var i = 0;
    while i < 7 {
        v = v * 256;
        if i < n {
            v = v + int_of(key[4 + i]);
        }
        i = i + 1;
    }
    return v;
}

// The `n`th field of two keys, compared.
fn compare_field[&a, &b](x: &a [byte], y: &b [byte], n: int) -> [] int {
    var px = 0;
    var py = 0;
    var j = 0;
    while j < n {
        px = px + 4 + length_at(x, px);
        py = py + 4 + length_at(y, py);
        j = j + 1;
    }
    let lx = length_at(x, px);
    let ly = length_at(y, py);
    return bytes.compare(x[px + 4..px + 4 + lx], y[py + 4..py + 4 + ly]);
}

// Fill `order[0..n]` with the groups' numbers in output order (n is the number
// of groups): by group column `field` or integer `slot` (see `before`), else
// by key; a bottom-up merge sort through `spare`, stable and O(n log n).
pub fn sort_into[&g, &o, &s, &p](g: &g Groups, order: &!o [int], spare: &!s [int], pre: &!p [int], fields: int, field: int, slot: int, descending: bool) -> [] int {
    let n = map.size(g.index);
    var i = 0;
    while i < n {
        order[i] = i;
        pre[i] = prefix_of(map.key_at(g.index, i), fields);
        i = i + 1;
    }
    var width = 1;
    while width < n {
        var lo = 0;
        while lo < n {
            var mid = lo + width;
            if mid > n {
                mid = n;
            }
            var hi = lo + 2 * width;
            if hi > n {
                hi = n;
            }
            var p = lo;
            var q = mid;
            var k = lo;
            while k < hi {
                if p < mid && (q >= hi || !before(g.index, g.acc, pre, g.stride, fields, field, slot, descending, order[q], order[p])) {
                    spare[k] = order[p];
                    p = p + 1;
                } else {
                    spare[k] = order[q];
                    q = q + 1;
                }
                k = k + 1;
            }
            lo = hi;
        }
        var c = 0;
        while c < n {
            order[c] = spare[c];
            c = c + 1;
        }
        width = width * 2;
    }
    return 0;
}

fn append_int[&h](heap: &!h Heap, out: buffer.Buffer, v: int) -> [heap] buffer.Buffer {
    if v >= 0 {
        return buffer.push_nat(heap, out, v);
    }
    if v == query.int_min() {
        return buffer.append(heap, out, "-9223372036854775808");
    }
    var o = buffer.push(heap, out, byte_of('-'));
    return buffer.push_nat(heap, o, 0 - v);
}

// The value of aggregate `k` (function `function`) of group `x`.
pub fn value_of[&g](g: &g Groups, x: int, k: int, function: int) -> [] int {
    var slot = 1 + k;
    if function == 0 {
        slot = 0;
    }
    return vec.get(g.acc, x * g.stride + slot);
}

// One group as a row, into `out`: csv (fields joined by `delim`, quoted as
// `writer.csv_value` does -- here passed in as `quote`, see table.ls) or json
// (an array of strings, numbers written as decimal strings).
pub fn row_json[&h, &g, &q](heap: &!h Heap, out: buffer.Buffer, g: &g Groups, tree: &q query.Query, x: int) -> [heap] buffer.Buffer {
    var o = buffer.push(heap, out, byte_of('['));
    let key = map.key_at(g.index, x);
    var at = 0;
    var j = 0;
    let ng = query.count_of(tree, 2);
    while j < ng {
        let n = length_at(key, at);
        if j > 0 {
            o = buffer.push(heap, o, byte_of(','));
        }
        o = text.append_json(heap, o, key[at + 4..at + 4 + n]);
        at = at + 4 + n;
        j = j + 1;
    }
    var k = 0;
    while k < query.agg_count(tree) {
        if ng + k > 0 {
            o = buffer.push(heap, o, byte_of(','));
        }
        o = buffer.push(heap, o, byte_of(34));
        o = append_int(heap, o, value_of(g, x, k, query.agg_at(tree, k, 0)));
        o = buffer.push(heap, o, byte_of(34));
        k = k + 1;
    }
    return buffer.push(heap, o, byte_of(']'));
}

// The group's key fields, one at a time: field `j` of group `x`.
pub fn key_field[&g](g: &g Groups, x: int, j: int) -> [] &g [byte] {
    let key = map.key_at(g.index, x);
    var at = 0;
    var i = 0;
    while i < j {
        at = at + 4 + length_at(key, at);
        i = i + 1;
    }
    let n = length_at(key, at);
    return key[at + 4..at + 4 + n];
}

pub fn put_int[&h](heap: &!h Heap, out: buffer.Buffer, v: int) -> [heap] buffer.Buffer {
    return append_int(heap, out, v);
}

// `--agg`: `count`, `sum:COL`, `min:COL`, `max:COL`, `distinct:COL`, separated by
// commas (the escapes of `--select`'s list apply); COL is a name or `#N`. Added
// to the plan. Answers the plan and -1 when it was whole, -2 when it was not a
// list, else the number of the item that is not an aggregate.
pub fn parse_aggs[&h, &s](heap: &!h Heap, tree: query.Query, given: &s [byte]) -> [heap] (query.Query, int) {
    let (toks, ends, kinds, listed) = plan.parse(heap, given);
    var q = tree;
    var bad = -1;
    if !listed {
        bad = -2;
    }
    var i = 0;
    var from = 0;
    var n = 0;
    borrow ends as &er in {
        n = vec.size(er);
    }
    while i < n && bad == -1 {
        var to = 0;
        borrow ends as &er in {
            to = vec.get(er, i);
        }
        borrow toks as &tr in {
            let item = buffer.bytes(tr)[from..to];
            var colon = index_of_byte(item, byte_of(':'));
            if bytes.equal(item, "count") {
                q = query.add_agg(heap, q, 0, -1);
            } else if colon <= 0 || colon + 1 >= len(item) {
                bad = i;
            } else {
                let word = item[0..colon];
                var function = 0;
                if bytes.equal(word, "sum") {
                    function = 1;
                } else if bytes.equal(word, "min") {
                    function = 2;
                } else if bytes.equal(word, "max") {
                    function = 3;
                } else if bytes.equal(word, "distinct") {
                    function = 4;
                }
                if function == 0 {
                    bad = i;
                } else {
                    let column = item[colon + 1..len(item)];
                    let (q2, at) = query.add_name(heap, q, column, query.position_of(column));
                    let q3 = query.add_agg(heap, q2, function, at);
                    q = q3;
                }
            }
        }
        from = to;
        i = i + 1;
    }
    buffer.drop(heap, toks);
    vec.drop(heap, ends);
    vec.drop(heap, kinds);
    return (q, bad);
}

// ---------------------------------------------------------------------------------
// Groups across ranges (par.ls). A worker counts its range into groups of its own and
// writes them out as bytes; the parent reads those bytes into its own groups, range by
// range, in file order. The bytes are, little-endian:
//
//     groups: i64, pairs: i64,
//     then each group: key length u32, the key, `stride` i64 (the group's integers),
//     then each distinct pair: key length u32, the key (group number u32, the aggregate's
//     number u8, the value's length and bytes).
//
// What a merge may and may not do is what keeps the answer the sequential one:
// the groups are a set, so their order does not matter, and counts, minima, maxima and
// distinct values combine in any order; a sum does not, because the sequential read
// refuses at the first row where a *running* sum leaves 64 bits, and a range's sum
// starting from zero says nothing of that. So a worker also keeps, per group and sum,
// the largest absolute value its running sum took (`peak`), and the merge of a range
// into a group that already has a total `S` is allowed only when `|S| + peak` fits: then
// no running sum of the sequential read, which is `S` plus one of the range's, can have
// left 64 bits. Otherwise the merge refuses and the parent reads the range again itself.
// So does it for a bound that would be passed (groups, distinct values, key bytes): the
// sequential read says which refusal comes first only by reading in order.
// ---------------------------------------------------------------------------------

fn put_u8[&o](out: &!o [byte], at: int, v: int) -> [] int {
    out[at] = byte_of(v & 255);
    return at + 1;
}

fn put_u32[&o](out: &!o [byte], at: int, v: int) -> [] int {
    out[at] = byte_of(v & 255);
    out[at + 1] = byte_of(v >> 8 & 255);
    out[at + 2] = byte_of(v >> 16 & 255);
    out[at + 3] = byte_of(v >> 24 & 255);
    return at + 4;
}

pub fn put_i64[&o](out: &!o [byte], at: int, v: int) -> [] int {
    var i = 0;
    while i < 8 {
        out[at + i] = byte_of(v >> 8 * i & 255);
        i = i + 1;
    }
    return at + 8;
}

// The i64 at `at`, and nothing wraps: the high half is signed, and half times 2^32 plus
// the low half fits whatever the half was.
pub fn get_i64[&b](blob: &b [byte], at: int) -> [] int {
    let low = int_of(blob[at]) | int_of(blob[at + 1]) << 8 | int_of(blob[at + 2]) << 16 | int_of(blob[at + 3]) << 24;
    var top = int_of(blob[at + 7]);
    if top >= 128 {
        top = top - 256;
    }
    let high = int_of(blob[at + 4]) | int_of(blob[at + 5]) << 8 | int_of(blob[at + 6]) << 16;
    return (high + top * 16777216) * 4294967296 + low;
}

fn put_bytes[&o, &d](out: &!o [byte], at: int, data: &d [byte]) -> [] int {
    var i = 0;
    while i < len(data) {
        out[at + i] = data[i];
        i = i + 1;
    }
    return at + len(data);
}

// Write the groups into `out` (which has `len(out)` bytes). Answers the bytes used, or
// -1 when they do not fit.
pub fn serialize[&g, &o](g: &g Groups, out: &!o [byte]) -> [] int {
    let ngroups = map.size(g.index);
    let npairs = map.size(g.seen);
    var at = 16;
    if at > len(out) {
        return 0 - 1;
    }
    put_i64(out, 0, ngroups);
    put_i64(out, 8, npairs);
    var e = 0;
    while e < ngroups {
        let key = map.key_at(g.index, e);
        if at + 4 + len(key) + 8 * g.stride > len(out) {
            return 0 - 1;
        }
        at = put_u32(out, at, len(key));
        at = put_bytes(out, at, key);
        var i = 0;
        while i < g.stride {
            at = put_i64(out, at, vec.get(g.acc, e * g.stride + i));
            i = i + 1;
        }
        e = e + 1;
    }
    var p = 0;
    while p < npairs {
        let key = map.key_at(g.seen, p);
        if at + 4 + len(key) > len(out) {
            return 0 - 1;
        }
        at = put_u32(out, at, len(key));
        at = put_bytes(out, at, key);
        p = p + 1;
    }
    return at;
}

// Read the groups in `blob` (written by `serialize`, for a plan of `tree`) into `g`.
// Answers the groups and 0, or the groups untouched and 1 when the answer of the
// sequential read could differ (see above).
pub fn merge[&h, &b, &q](heap: &!h Heap, g: Groups, blob: &b [byte], tree: &q query.Query, max_groups: int, max_distinct: int, max_state: int) -> [heap] (Groups, int) {
    let Groups { index, acc, seen, keyb, dkey, held, pairs, stride } = g;
    let na = query.agg_count(tree);
    let ngroups = get_i64(blob, 0);
    let npairs = get_i64(blob, 8);
    let mapping = box_slice(heap, ngroups + 1, 0 - 1);
    let fresh = box_slice(heap, ngroups * na + 1, 0);
    var m = index;
    var a = acc;
    var s = seen;
    var scratch = dkey;
    var refuse = false;
    var new_groups = 0;
    var new_pairs = 0;
    var new_bytes = 0;
    var applied = false;
    borrow mut mapping as &!mw in {
        borrow mut fresh as &!fw in {
            let mp = contents(mw);
            let fr = contents(fw);
            // First: what would it add, and is it safe to add it?
            var at = 16;
            var i = 0;
            while i < ngroups && !refuse {
                let klen = length_at(blob, at);
                let key = blob[at + 4..at + 4 + klen];
                let acc_at = at + 4 + klen;
                var found = -1;
                borrow m as &mr in {
                    found = map.find(mr, key);
                }
                mp[i] = found;
                if found < 0 {
                    new_groups = new_groups + 1;
                    new_bytes = new_bytes + klen;
                } else {
                    var k = 0;
                    while k < na {
                        if query.agg_at(tree, k, 0) == 1 {
                            var total = 0;
                            borrow a as &ar in {
                                total = vec.get(ar, found * stride + 1 + k);
                            }
                            if get_i64(blob, acc_at + 8 * (1 + na + k)) > query.int_max() - int_max_of(total) {
                                refuse = true;
                            }
                        }
                        k = k + 1;
                    }
                }
                at = acc_at + 8 * stride;
                i = i + 1;
            }
            var pair_at = at;
            var p = 0;
            while p < npairs && !refuse {
                let klen = length_at(blob, pair_at);
                let key = blob[pair_at + 4..pair_at + 4 + klen];
                let gid = length_at(key, 0);
                let function_k = int_of(key[4]);
                let target = mp[gid];
                var known = false;
                if target >= 0 {
                    borrow mut scratch as &!sw in {
                        buffer.clear(sw);
                    }
                    scratch = put_length(heap, scratch, target);
                    scratch = buffer.append(heap, scratch, key[4..len(key)]);
                    borrow scratch as &sr in {
                        borrow s as &seen_r in {
                            known = map.find(seen_r, buffer.bytes(sr)) >= 0;
                        }
                    }
                }
                if !known {
                    new_pairs = new_pairs + 1;
                    new_bytes = new_bytes + klen;
                    fr[gid * na + function_k] = fr[gid * na + function_k] + 1;
                }
                pair_at = pair_at + 4 + klen;
                p = p + 1;
            }
            var size = 0;
            borrow m as &mr in {
                size = map.size(mr);
            }
            if size + new_groups > max_groups || pairs + new_pairs > max_distinct || held + new_bytes > max_state {
                refuse = true;
            }
            if !refuse {
                // Then: add it.
                at = 16;
                i = 0;
                while i < ngroups {
                    let klen = length_at(blob, at);
                    let key = blob[at + 4..at + 4 + klen];
                    let acc_at = at + 4 + klen;
                    var entry = mp[i];
                    if entry < 0 {
                        borrow m as &mr in {
                            entry = map.size(mr);
                        }
                        m = map.put(heap, m, key, 0);
                        mp[i] = entry;
                        var z = 0;
                        while z < stride {
                            a = vec.push(heap, a, get_i64(blob, acc_at + 8 * z));
                            z = z + 1;
                        }
                    } else {
                        var before = 0;
                        borrow a as &ar in {
                            before = vec.get(ar, entry * stride);
                        }
                        a = set_at(a, entry * stride, before + get_i64(blob, acc_at));
                        var k = 0;
                        while k < na {
                            let function = query.agg_at(tree, k, 0);
                            var mine = 0;
                            borrow a as &ar in {
                                mine = vec.get(ar, entry * stride + 1 + k);
                            }
                            let theirs = get_i64(blob, acc_at + 8 * (1 + k));
                            if function == 1 {
                                let (sum, fits) = query.add_checked(mine, theirs);
                                a = set_at(a, entry * stride + 1 + k, sum);
                            } else if function == 2 && theirs < mine {
                                a = set_at(a, entry * stride + 1 + k, theirs);
                            } else if function == 3 && theirs > mine {
                                a = set_at(a, entry * stride + 1 + k, theirs);
                            } else if function == 4 {
                                a = set_at(a, entry * stride + 1 + k, mine + fr[i * na + k]);
                            }
                            k = k + 1;
                        }
                    }
                    at = acc_at + 8 * stride;
                    i = i + 1;
                }
                pair_at = at;
                p = 0;
                while p < npairs {
                    let klen = length_at(blob, pair_at);
                    let key = blob[pair_at + 4..pair_at + 4 + klen];
                    let gid = length_at(key, 0);
                    borrow mut scratch as &!sw in {
                        buffer.clear(sw);
                    }
                    scratch = put_length(heap, scratch, mp[gid]);
                    scratch = buffer.append(heap, scratch, key[4..len(key)]);
                    borrow scratch as &sr in {
                        var known = false;
                        borrow s as &seen_r in {
                            known = map.find(seen_r, buffer.bytes(sr)) >= 0;
                        }
                        if !known {
                            s = map.put(heap, s, buffer.bytes(sr), 0);
                        }
                    }
                    pair_at = pair_at + 4 + klen;
                    p = p + 1;
                }
                applied = true;
            }
        }
    }
    unbox_slice(heap, mapping);
    unbox_slice(heap, fresh);
    if !applied {
        return (Groups { index: m, acc: a, seen: s, keyb: keyb, dkey: scratch, held: held, pairs: pairs, stride: stride }, 1);
    }
    return (Groups { index: m, acc: a, seen: s, keyb: keyb, dkey: scratch, held: held + new_bytes, pairs: pairs + new_pairs, stride: stride }, 0);
}
