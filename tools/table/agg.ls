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
// Each group has `1 + 2 * aggregates` integers: its row count, then one per
// aggregate: the sum, the minimum, the maximum, or the number of distinct
// values; then, for each aggregate, a second one that only a sum uses. Sum, min
// and max need every cell they see to be an exact integer (an empty cell is not:
// filter it out with --where first). A **sum is a pair**: the low 32 bits of every
// cell are added in the aggregate's own integer and the rest (the cell shifted right
// by 32, signed) in its second one, so the sum is `hi * 2^32 + lo` and nothing can
// leave 64 bits for as many as 10^9 rows (`--max-rows`' ceiling): `lo` stays below 2^62
// and `hi` below 2^61. There is no refusal and no wrap, a sum may be wider than an `int`
// (it is printed in full: `put_sum`), and the sum of ranges read by threads is the same
// pair, added in any order (docs/numbers.md, stage N0p). `distinct` counts bytes, and keeps
// every (group, value) pair it has seen, which is what `--max-distinct` bounds.
//
// Three bounds, each its own refusal: groups (`--max-groups`), distinct pairs
// (`--max-distinct`), and the bytes of every key held (`--max-state-bytes`),
// because a count of keys bounds memory only if the keys are short.

import std.buffer;
import std.bytes;
import std.map;
import std.vec;
import dec;
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
    // A small cache from a cheap look at the key's cells to a group's entry number plus one (0: none):
    // a row whose group is the one the cache names is added without hashing the key (`add_fast`).
    memo: Box[[int]],
}

pub fn memo_size() -> [] int {
    return 1024;
}

pub fn start[&h](heap: &!h Heap, aggregates: int) -> [heap] Groups {
    return Groups { index: map.empty(heap, 64, 0, 0x5eed), acc: vec.empty(heap, 64, 0), seen: map.empty(heap, 4, 0, 0x5eed), keyb: buffer.empty(heap, 64), dkey: buffer.empty(heap, 64), held: 0, pairs: 0, stride: 1 + 2 * aggregates, memo: box_slice(heap, memo_size() + 4, 0) };
}

pub fn drop[&h](heap: &!h Heap, g: Groups) -> [heap] int {
    let Groups { index, acc, seen, keyb, dkey, held, pairs, stride, memo } = g;
    map.drop(heap, index);
    vec.drop(heap, acc);
    map.drop(heap, seen);
    buffer.drop(heap, keyb);
    buffer.drop(heap, dkey);
    unbox_slice(heap, memo);
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

// A field as a number of the type `kind` (1 :int, 2 + S :dec(S)): (value, status, scratch), status 0 ok, 4 not an integer, 5 past 64 bits,
// 6 not a decimal, 7 more fractional digits than the scale, 8 too wide (`query.parse_typed`).
fn typed_cell[&h, &d](heap: &!h Heap, scratch: buffer.Buffer, record: &d [byte], first: int, last: int, quoted: int, kind: int) -> [heap] (int, int, buffer.Buffer) {
    var scr = scratch;
    if quoted == 1 && index_of_byte(record[first..last], byte_of(34)) >= 0 {
        borrow mut scr as &!sw in {
            buffer.clear(sw);
        }
        scr = query.unquote(heap, scr, record[first..last]);
        var v = 0;
        var bad = 0;
        borrow scr as &sr in {
            let (x, y) = query.parse_typed(buffer.bytes(sr), kind);
            v = x;
            bad = y;
        }
        return (v, bad, scr);
    }
    let (v, bad) = query.parse_typed(record[first..last], kind);
    return (v, bad, scr);
}

// The 8 bytes that stand for a scaled value as a distinct key, so that `1.5` and `1.50` are one: the value plus 2^63, big endian
// (the same bytes order as the numbers do).
fn put_value_key[&h](heap: &!h Heap, out: buffer.Buffer, v: int) -> [heap] buffer.Buffer {
    var o = out;
    let biased = v ^ (0 - 9223372036854775807 - 1);
    var i = 7;
    while i >= 0 {
        o = buffer.push(heap, o, byte_of(biased >> 8 * i & 255));
        i = i - 1;
    }
    return o;
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
// not an integer; 5 an integer of more than 64 bits; 6, 7, 8 a decimal that is not one, has too many fractional digits, is too wide.
pub fn add[&h, &q, &c, &d, &e](heap: &!h Heap, g: Groups, tree: &q query.Query, cols: &c [int], record: &d [byte], cells: &e [int], scratch: buffer.Buffer, max_groups: int, max_distinct: int, max_state: int, keyed: bool) -> [heap] (Groups, buffer.Buffer, int, int) {
    let Groups { index, acc, seen, keyb, dkey, held, pairs, stride, memo } = g;
    var key = keyb;
    var scr = scratch;
    var m = index;
    var a = acc;
    var s = seen;
    var dk = dkey;
    var bytes_held = held;
    var distinct_pairs = pairs;
    // `keyed`: the key is already in the key buffer (`add_fast` built it and did not find it).
    if !keyed {
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
    }
    var status = 0;
    var which = 0;
    var entry = -1;
    var size = 0;
    borrow key as &kr in {
        borrow m as &mr in {
            if !keyed {
                entry = map.find(mr, buffer.bytes(kr));
            }
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
                    if query.agg_at(tree, k, 2) != 0 {
                        // by value: a decimal's 8 bytes, whatever its text
                        let (dv, dbad, s4) = typed_cell(heap, scr, record, first, last, quoted, query.agg_at(tree, k, 2));
                        scr = s4;
                        if dbad != 0 {
                            status = dbad;
                        } else {
                            dk = put_value_key(heap, dk, dv);
                        }
                    } else {
                        let (d2, s2) = put_cell(heap, dk, scr, record, first, last, quoted);
                        dk = d2;
                        scr = s2;
                    }
                    var known = false;
                    if status == 0 {
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
                    }
                    if status == 0 && !known {
                        a = set_at(a, at + 1 + k, now + 1);
                    }
                } else {
                    let (v, bad, s3) = typed_cell(heap, scr, record, first, last, quoted, query.agg_at(tree, k, 2));
                    scr = s3;
                    if bad != 0 {
                        status = bad;
                    } else if function == 1 {
                        var high = 0;
                        borrow a as &ar in {
                            high = vec.get(ar, at + 1 + (stride - 1) / 2 + k);
                        }
                        a = set_at(a, at + 1 + k, now + (v & 0xffffffff));
                        a = set_at(a, at + 1 + (stride - 1) / 2 + k, high + (v >> 32));
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
    return (Groups { index: m, acc: a, seen: s, keyb: key, dkey: dk, held: bytes_held, pairs: distinct_pairs, stride: stride, memo: memo }, scr, status, which);
}

// The same as `add` for the row that needs nothing `add` has to move: a group that exists, keys and
// aggregates that are plain bytes and integers, the key fitting the room the key buffer has. Works through
// a unique reference to the groups, so the state is not moved in and out of a call for each row (a `Groups`
// is some forty words). Answers (status, which) as `add` does, or (-1, 0) when the row has to take `add`'s
// way; in that case nothing was changed but the key buffer, which `add` clears anyway.
pub fn add_fast[&g, &q, &c, &d, &e](g: &!g Groups, tree: &q query.Query, cols: &c [int], record: &d [byte], cells: &e [int]) -> [] (int, int) {
    // A run of rows that are each a new group (a key that is nearly unique) gets nothing from this way, which
    // builds the key and looks for it before `add` puts it: after 32 in a row the next 2048 rows take `add`'s
    // way at once. `memo[size + 2]` is how many are left, `memo[size + 3]` the run so far.
    let rest_at = memo_size() + 2;
    let waiting = contents(g.memo)[rest_at];
    if waiting > 0 {
        contents(g.memo)[rest_at] = waiting - 1;
        return (0 - 1, 0);
    }
    let ng = query.count_of(tree, 2);
    let base = query.count_of(tree, 0) + query.count_of(tree, 1);
    let na = query.agg_count(tree);
    var need = 0;
    var j = 0;
    while j < ng {
        let column = cols[base + j];
        let first = cells[3 * column];
        let last = cells[3 * column + 1];
        if cells[3 * column + 2] == 1 && index_of_byte(record[first..last], byte_of(34)) >= 0 {
            return (0 - 1, 0);
        }
        need = need + 4 + last - first;
        j = j + 1;
    }
    var k = 0;
    while k < na {
        let function = query.agg_at(tree, k, 0);
        if function == 4 {
            return (0 - 1, 0);
        }
        if function != 0 {
            let column = cols[query.agg_at(tree, k, 1)];
            if cells[3 * column + 2] == 1 && index_of_byte(record[cells[3 * column]..cells[3 * column + 1]], byte_of(34)) >= 0 {
                return (0 - 1, 0);
            }
        }
        k = k + 1;
    }
    // The cache: a cheap look at each cell (its length, first and last byte) picks a place; what is there
    // is a group's entry number, and is believed only when its key is these cells. It is a cache for
    // groups that come back, so it gives itself up for a while when it keeps missing (a key that is
    // nearly always new, or one of very many): `memo[size]` is the score, `memo[size + 1]` the rows
    // still to be done without it.
    let size = memo_size();
    var entry = 0 - 1;
    var place = 0;
    var resting = contents(g.memo)[size + 1];
    if resting > 0 {
        contents(g.memo)[size + 1] = resting - 1;
    } else {
        var look = 0;
        j = 0;
        while j < ng {
            let column = cols[base + j];
            let first = cells[3 * column];
            let n = cells[3 * column + 1] - first;
            look = look * 31 + n;
            if n > 0 {
                look = (look * 31 + int_of(record[first])) * 31 + int_of(record[first + n - 1]);
            }
            j = j + 1;
        }
        place = (look ^ look >> 11) & size - 1;
        entry = contents(g.memo)[place] - 1;
        if entry >= 0 {
            let key = map.key_at(g.index, entry);
            var p = 0;
            j = 0;
            while j < ng && entry >= 0 {
                let column = cols[base + j];
                let first = cells[3 * column];
                let n = cells[3 * column + 1] - first;
                if length_at(key, p) != n || !bytes.equal(key[p + 4..p + 4 + n], record[first..first + n]) {
                    entry = 0 - 1;
                }
                p = p + 4 + n;
                j = j + 1;
            }
        }
        var score = contents(g.memo)[size];
        if entry >= 0 {
            if score < 32 {
                contents(g.memo)[size] = score + 1;
            }
        } else if score < 0 - 16 {
            contents(g.memo)[size] = 0;
            contents(g.memo)[size + 1] = 4096;
        } else {
            contents(g.memo)[size] = score - 1;
        }
    }
    if entry < 0 {
        buffer.clear(g.keyb);
        if len(buffer.room(g.keyb)) < need {
            return (0 - 1, 0);
        }
        let room = buffer.room(g.keyb);
        var at = 0;
        j = 0;
        while j < ng {
            let column = cols[base + j];
            let first = cells[3 * column];
            let n = cells[3 * column + 1] - first;
            room[at] = byte_of(n & 255);
            room[at + 1] = byte_of(n >> 8 & 255);
            room[at + 2] = byte_of(n >> 16 & 255);
            room[at + 3] = byte_of(n >> 24 & 255);
            copy_into(room[at + 4..at + 4 + n], record[first..first + n]);
            at = at + 4 + n;
            j = j + 1;
        }
        buffer.filled(g.keyb, need);
        entry = map.find(g.index, buffer.bytes(g.keyb));
        if entry < 0 {
            let run = contents(g.memo)[rest_at + 1] + 1;
            if run >= 32 {
                contents(g.memo)[rest_at] = 2048;
                contents(g.memo)[rest_at + 1] = 0;
            } else {
                contents(g.memo)[rest_at + 1] = run;
            }
            return (0 - 2, 0);
        }
        if contents(g.memo)[rest_at + 1] > 0 {
            contents(g.memo)[rest_at + 1] = 0;
        }
        if resting <= 0 {
            contents(g.memo)[place] = entry + 1;
        }
    }
    let stride = g.stride;
    let slot = entry * stride;
    let before = vec.get(g.acc, slot);
    vec.set(g.acc, slot, before + 1);
    k = 0;
    // The cell an aggregate reads is read once for the ones that follow it on the same column and type (`sum, min, max, mean` of one column).
    var seen_column = 0 - 1;
    var seen_kind = 0;
    var v = 0;
    var bad = 0;
    while k < na {
        let function = query.agg_at(tree, k, 0);
        if function != 0 {
            let column = cols[query.agg_at(tree, k, 1)];
            let now = vec.get(g.acc, slot + 1 + k);
            let kind = query.agg_at(tree, k, 2);
            if column != seen_column || kind != seen_kind {
                let (v2, bad2) = query.parse_typed(record[cells[3 * column]..cells[3 * column + 1]], kind);
                v = v2;
                bad = bad2;
                seen_column = column;
                seen_kind = kind;
            }
            if bad != 0 {
                return (bad, k);
            }
            if function == 1 {
                let high_at = slot + 1 + (stride - 1) / 2 + k;
                vec.set(g.acc, slot + 1 + k, now + (v & 0xffffffff));
                vec.set(g.acc, high_at, vec.get(g.acc, high_at) + (v >> 32));
            } else if before == 0 {
                vec.set(g.acc, slot + 1 + k, v);
            } else if function == 2 && v < now {
                vec.set(g.acc, slot + 1 + k, v);
            } else if function == 3 && v > now {
                vec.set(g.acc, slot + 1 + k, v);
            }
        }
        k = k + 1;
    }
    return (0, 0);
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
    if slot >= 1000000 {
        // A mean (the slot is the sum's, plus 1,000,000): by the exact quotient, sum over count, without dividing (`dec.compare_means`).
        let s = slot - 1000000;
        let off = (stride - 1) / 2;
        let c = dec.compare_means(vec.get(acc, x * stride + s + off), vec.get(acc, x * stride + s), vec.get(acc, x * stride), vec.get(acc, y * stride + s + off), vec.get(acc, y * stride + s), vec.get(acc, y * stride));
        if c != 0 {
            if descending {
                return c > 0;
            }
            return c < 0;
        }
        if fields >= 1 && pre[x] != pre[y] {
            return pre[x] < pre[y];
        }
        return compare_keys(map.key_at(index, x), map.key_at(index, y), fields) < 0;
    }
    if slot >= 1 {
        // A sum is (high, low), settled; the second integer of any other aggregate is 0.
        let hx = vec.get(acc, x * stride + slot + (stride - 1) / 2);
        let hy = vec.get(acc, y * stride + slot + (stride - 1) / 2);
        if hx != hy {
            if descending {
                return hx > hy;
            }
            return hx < hy;
        }
    }
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

// Carry the low half of every sum into the high half, so that `low` is in [0, 2^32): done once, when
// the groups are complete and before they are sorted or written. A sum is then `high * 2^32 + low`.
pub fn settle[&g, &q](g: &!g Groups, tree: &q query.Query) -> [] int {
    let n = map.size(g.index);
    let na = query.agg_count(tree);
    var x = 0;
    while x < n {
        var k = 0;
        while k < na {
            if query.agg_at(tree, k, 0) == 1 {
                let low_at = x * g.stride + 1 + k;
                let high_at = low_at + (g.stride - 1) / 2;
                let low = vec.get(g.acc, low_at);
                vec.set(g.acc, high_at, vec.get(g.acc, high_at) + (low >> 32));
                vec.set(g.acc, low_at, low & 0xffffffff);
            }
            k = k + 1;
        }
        x = x + 1;
    }
    return 0;
}

// The value of aggregate `k` of group `x`, as text: a count or a distinct count as an integer; a sum, minimum or maximum at the scale of its
// column (an integer has scale 0), a sum of any width (`dec.put_sum`); a mean from the exact sum and the group's count, rounded half to even at
// its own scale (`dec.put_mean`).
pub fn put_value[&h, &g, &q](heap: &!h Heap, out: buffer.Buffer, g: &g Groups, tree: &q query.Query, x: int, k: int) -> [heap] buffer.Buffer {
    let function = query.agg_at(tree, k, 0);
    var scale = 0;
    if query.agg_at(tree, k, 2) >= 2 {
        scale = query.agg_at(tree, k, 2) - 2;
    }
    if function == 1 {
        let high = vec.get(g.acc, x * g.stride + 1 + k + (g.stride - 1) / 2);
        let low = vec.get(g.acc, x * g.stride + 1 + k);
        let mean = query.agg_at(tree, k, 3);
        if mean == 0 {
            return dec.put_sum(heap, out, high, low, scale);
        }
        var to = scale;
        if mean != 100 {
            to = mean - 1;
        }
        return dec.put_mean(heap, out, high, low, vec.get(g.acc, x * g.stride), scale, to);
    }
    if (function == 2 || function == 3) && query.agg_at(tree, k, 2) >= 2 {
        return dec.put_scaled(heap, out, value_of(g, x, k, function), scale);
    }
    return append_int(heap, out, value_of(g, x, k, function));
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
        o = put_value(heap, o, g, tree, x, k);
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

// Whether the byte at `p` is escaped: an odd number of backslashes before it.
fn escaped_at[&d](data: &d [byte], p: int) -> [] bool {
    var n = 0;
    var i = p - 1;
    while i >= 0 && int_of(data[i]) == '\\' {
        n = n + 1;
        i = i - 1;
    }
    return n % 2 == 1;
}

// The tail of one raw item of `--agg` (docs/numbers.md 5.1): the length of what is left of it, its type (0 none, 1 `:int`, 2 + S `:dec(S)`,
// 99 a scale that is not 0 to 18) and its mean (0 none, 1 + N for `@N`, 200 for an N past 18). A tail is only a tail when an unescaped colon
// is left before it, the one that follows the function: `sum:int` is the sum of a column called `int`, `sum:x:int` the integer sum of `x`, and
// a column really called `x:int` is written `sum:x\:int` (and one called `x@2`, `mean:x\@2`).
fn tail_of[&d](item: &d [byte]) -> [] (int, int, int) {
    var e = len(item);
    var kind = 0;
    var mean = 0;
    var k = e;
    var digits = 0;
    while k > 0 && digits < 3 && bytes.is_digit(int_of(item[k - 1])) {
        k = k - 1;
        digits = digits + 1;
    }
    if digits >= 1 && digits <= 2 && k >= 1 && int_of(item[k - 1]) == '@' && !escaped_at(item, k - 1) {
        var v = 0;
        var j = k;
        while j < e {
            v = v * 10 + int_of(item[j]) - '0';
            j = j + 1;
        }
        mean = 1 + v;
        if v > 18 {
            mean = 200;
        }
        e = k - 1;
    }
    if e >= 4 && bytes.equal(item[e - 4..e], ":int") && !escaped_at(item, e - 4) && index_of_byte(item[0..e - 4], byte_of(':')) > 0 {
        kind = 1;
        e = e - 4;
    } else if e >= 7 && int_of(item[e - 1]) == ')' {
        var k2 = e - 1;
        var d2 = 0;
        while k2 > 0 && d2 < 3 && bytes.is_digit(int_of(item[k2 - 1])) {
            k2 = k2 - 1;
            d2 = d2 + 1;
        }
        if d2 >= 1 && d2 <= 2 && k2 >= 5 && int_of(item[k2 - 1]) == '(' && bytes.equal(item[k2 - 5..k2], ":dec(") && !escaped_at(item, k2 - 5) && index_of_byte(item[0..k2 - 5], byte_of(':')) > 0 {
            var v = 0;
            var j = k2;
            while j < e - 1 {
                v = v * 10 + int_of(item[j]) - '0';
                j = j + 1;
            }
            kind = 2 + v;
            if v > 18 {
                kind = 99;
            }
            e = k2 - 5;
        }
    }
    return (e, kind, mean);
}

// `--agg`: `count`, `sum:COL`, `min:COL`, `max:COL`, `mean:COL`, `distinct:COL`, separated by commas (the escapes of `--select`'s list apply,
// and so do `\:` and `\@`); COL is a name or `#N`, and may end in `:int` or `:dec(S)` (how its cells are read: a sum, minimum, maximum and mean
// read an untyped COL as `:int`, a `distinct` as text), and a `mean` in `@N` (the scale of the mean: required unless COL is `:dec(S)`, whose own
// scale is the default). Added to the plan. Answers the plan and -1 when it was whole, -2 when it was not a list, else the number of the item
// that is not an aggregate.
pub fn parse_aggs[&h, &s](heap: &!h Heap, tree: query.Query, given: &s [byte]) -> [heap] (query.Query, int) {
    var cleaned = buffer.empty(heap, len(given) + 1);
    var tails = vec.empty(heap, 8, 0);
    let n = len(given);
    var start = 0;
    var more = true;
    while more {
        var j = start;
        while j < n && int_of(given[j]) != ',' {
            if int_of(given[j]) == '\\' && j + 1 < n {
                j = j + 2;
            } else {
                j = j + 1;
            }
        }
        let item = given[start..j];
        let (e, kind, mean) = tail_of(item);
        tails = vec.push(heap, tails, kind);
        tails = vec.push(heap, tails, mean);
        var p = 0;
        while p < e {
            let c = int_of(item[p]);
            if c == '\\' && p + 1 < len(item) {
                let d = int_of(item[p + 1]);
                if d == ':' || d == '@' {
                    cleaned = buffer.push(heap, cleaned, byte_of(d));
                } else {
                    cleaned = buffer.push(heap, cleaned, byte_of(c));
                    cleaned = buffer.push(heap, cleaned, byte_of(d));
                }
                p = p + 2;
            } else {
                cleaned = buffer.push(heap, cleaned, byte_of(c));
                p = p + 1;
            }
        }
        if j < n {
            cleaned = buffer.push(heap, cleaned, byte_of(','));
            start = j + 1;
        } else {
            more = false;
        }
    }
    var toks = buffer.empty(heap, 1);
    var ends = vec.empty(heap, 1, 0);
    var kinds = vec.empty(heap, 1, 0);
    var listed = true;
    buffer.drop(heap, toks);
    vec.drop(heap, ends);
    vec.drop(heap, kinds);
    borrow cleaned as &cr in {
        let (t2, e2, k2, l2) = plan.parse(heap, buffer.bytes(cr));
        toks = t2;
        ends = e2;
        kinds = k2;
        listed = l2;
    }
    buffer.drop(heap, cleaned);
    var q = tree;
    var bad = -1;
    if !listed {
        bad = -2;
    }
    var i = 0;
    var from = 0;
    var count = 0;
    borrow ends as &er in {
        count = vec.size(er);
    }
    while i < count && bad == -1 {
        var to = 0;
        borrow ends as &er in {
            to = vec.get(er, i);
        }
        var kind = 0;
        var mean = 0;
        borrow tails as &tr in {
            if 2 * i + 1 < vec.size(tr) {
                kind = vec.get(tr, 2 * i);
                mean = vec.get(tr, 2 * i + 1);
            }
        }
        borrow toks as &tr in {
            let item = buffer.bytes(tr)[from..to];
            let colon = index_of_byte(item, byte_of(':'));
            if bytes.equal(item, "count") {
                if kind != 0 || mean != 0 {
                    bad = i;
                } else {
                    q = query.add_agg(heap, q, 0, -1, 0, 0);
                }
            } else if colon <= 0 || colon + 1 >= len(item) || kind == 99 || mean == 200 {
                bad = i;
            } else {
                let word = item[0..colon];
                var function = 0;
                var is_mean = false;
                if bytes.equal(word, "sum") {
                    function = 1;
                } else if bytes.equal(word, "min") {
                    function = 2;
                } else if bytes.equal(word, "max") {
                    function = 3;
                } else if bytes.equal(word, "distinct") {
                    function = 4;
                } else if bytes.equal(word, "mean") {
                    function = 1;
                    is_mean = true;
                }
                var kind_final = kind;
                var mean_final = 0;
                if function == 0 {
                    bad = i;
                } else if is_mean {
                    // the scale is the column's when it is a decimal, and must be said otherwise
                    if kind < 2 && mean == 0 {
                        bad = i;
                    } else {
                        mean_final = mean;
                        if mean == 0 {
                            mean_final = 100;
                        }
                        if kind == 0 {
                            kind_final = 1;
                        }
                    }
                } else if mean != 0 {
                    bad = i;
                } else if function != 4 && kind == 0 {
                    kind_final = 1;
                }
                if bad == -1 {
                    let column = item[colon + 1..len(item)];
                    let (q2, at) = query.add_name(heap, q, column, query.position_of(column));
                    q = query.add_agg(heap, q2, function, at, kind_final, mean_final);
                }
            }
        }
        from = to;
        i = i + 1;
    }
    buffer.drop(heap, toks);
    vec.drop(heap, ends);
    vec.drop(heap, kinds);
    vec.drop(heap, tails);
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
// distinct values combine in any order, and so does a sum, which is a pair of integers
// that nothing can overflow (the first versions refused a sum past 64 bits, which a range
// read from zero could not tell, and kept a `peak` to merge only the ranges whose
// running sums could not have crossed the edge: gone with the refusal, docs/numbers.md N0p).
// What can still make the merge refuse, and the parent read the range again itself, is a bound that would be passed (groups, distinct values, key bytes): the
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
    let Groups { index, acc, seen, keyb, dkey, held, pairs, stride, memo } = g;
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
                                // a sum is a pair, and a pair adds: the order of the ranges is of no consequence
                                var mine_high = 0;
                                borrow a as &ar in {
                                    mine_high = vec.get(ar, entry * stride + 1 + na + k);
                                }
                                a = set_at(a, entry * stride + 1 + k, mine + theirs);
                                a = set_at(a, entry * stride + 1 + na + k, mine_high + get_i64(blob, acc_at + 8 * (1 + na + k)));
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
        return (Groups { index: m, acc: a, seen: s, keyb: keyb, dkey: scratch, held: held, pairs: pairs, stride: stride, memo: memo }, 1);
    }
    return (Groups { index: m, acc: a, seen: s, keyb: keyb, dkey: scratch, held: held + new_bytes, pairs: pairs + new_pairs, stride: stride, memo: memo }, 0);
}
