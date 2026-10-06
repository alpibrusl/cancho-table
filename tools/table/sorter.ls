edition 5;

module sorter;

// `--order-by`: the rows of the answer, held and sorted (docs/sort.md).
//
// The state is an `agg.Groups`, the same value the grouping holds, used for another purpose (the read gives rows to
// `engine.process_groups`, which hands them to `order_row` here when the plan has order keys). Why that and not a
// `Sorter` of its own: a third thing to do with a row in the read's loop, next to "write it" and "group it", made the
// compiler give up specialising the loop for the first two, and `--select` and `--where` were 15 percent slower (a branch that
// is never taken, at that place, did the same: docs/sort.md). So `keyb` is the data, `dkey` the side buffer, `acc` the
// index, `stride` the number of keys, `pairs` the number of rows held, and `memo` (a box of integers) the plan: `memo[0]`
// the rows wanted (0 for all), `memo[1]` 1 once the last of them is known, `memo[2]` and `memo[3]` the bounds on the rows and on the bytes,
// and from `memo[4]` three integers for each key.
//
// A row is kept as the record was read, in `data`; the index `rows` has `stride` integers a row: where the
// record begins in `data`, its length, its number in the file, then for each key two integers: for an
// integer key its value and 0; for a text key where its bytes are and how long they are, in `data` (an
// offset of 0 or more: the cell had no doubled quote) or in `side` (an offset `-1 - at`: the cell was quoted
// with doubled quotes, and what it says is kept there unquoted).
//
// The order of two rows is their keys, each ascending or descending; rows with equal keys keep the order they were
// held in, which is the order of the file (rows are appended as they are read, and a cut back keeps the sorted order),
// because the merge sort is stable.
//
// The three integers of a key are the column, 1 for descending, 1 for an integer.
//
// With `cap` above 0 only the `cap` first rows are wanted: once `2 * cap + 1` are held they are sorted and
// cut to `cap`, and a row that is not strictly before the last of those is not kept at all (`reject`: equal keys lose to the earlier row): memory is
// O(cap), whatever the file.

import std.buffer;
import std.bytes;
import std.vec;
import agg;
import query;

// Make the groups into a sorter of `nk` keys for `cap` wanted rows (0: all), the keys' columns and flags in `keys`.
pub fn configure[&k](g: agg.Groups, nk: int, cap: int, max_rows: int, max_state: int, keys: &k [int]) -> [] agg.Groups {
    let agg.Groups { index, acc, seen, keyb, dkey, held, pairs, stride, memo } = g;
    borrow mut memo as &!mw in {
        let m = contents(mw);
        m[0] = cap;
        m[1] = 0;
        m[2] = max_rows;
        m[3] = max_state;
        var j = 0;
        while j < 3 * nk {
            m[4 + j] = keys[j];
            j = j + 1;
        }
    }
    return agg.Groups { index: index, acc: acc, seen: seen, keyb: keyb, dkey: dkey, held: held, pairs: 0, stride: nk, memo: memo };
}

pub fn held[&s](s: &s agg.Groups) -> [] int {
    return s.pairs;
}

fn stride_of(nk: int) -> [] int {
    return 2 + 2 * nk;
}

// The bytes of a held row's record.
pub fn record_of[&s](s: &s agg.Groups, row: int) -> [] &s [byte] {
    let at = row * stride_of(s.stride);
    let off = vec.get(s.acc, at);
    return buffer.bytes(s.keyb)[off..off + vec.get(s.acc, at + 1)];
}

// The bytes of key `j` of a held row.
fn key_bytes[&s](s: &s agg.Groups, row: int, j: int) -> [] &s [byte] {
    let at = row * stride_of(s.stride) + 2 + 2 * j;
    let off = vec.get(s.acc, at);
    let n = vec.get(s.acc, at + 1);
    if off >= 0 {
        return buffer.bytes(s.keyb)[off..off + n];
    }
    let o = 0 - off - 1;
    return buffer.bytes(s.dkey)[o..o + n];
}

// -1, 0 or 1: the bytes of a candidate's key against a held row's, or its integer.
fn sign(c: int) -> [] int {
    if c < 0 {
        return 0 - 1;
    }
    if c > 0 {
        return 1;
    }
    return 0;
}

// Whether a row that is not yet held needs keeping, when the last of the `cap` wanted is known: 0 yes (it is
// before that one, or it cannot be told here), 1 no (it is not before it: equal keys lose to the earlier row).
// Nothing is changed and nothing is parsed that `add` would not parse again; a cell that is not an integer
// answers 0, and `add` refuses it.
pub fn reject[&s, &d, &c](s: &s agg.Groups, record: &d [byte], cells: &c [int]) -> [] int {
    if contents(s.memo)[1] == 0 {
        return 0;
    }
    let keys = contents(s.memo)[4..4 + 3 * s.stride];
    let last = contents(s.memo)[0] - 1;
    // 0 not decided yet, 1 keep it, 2 drop it. Every integer key is read either way: a cell that is not an integer is a
    // refusal whether the row would have been kept or not (the answer must not depend on the other rows).
    var decided = 0;
    var j = 0;
    while j < s.stride {
        let column = keys[3 * j];
        let first = cells[3 * column];
        let end = cells[3 * column + 1];
        var c = 0;
        if cells[3 * column + 2] == 1 && index_of_byte(record[first..end], byte_of(34)) >= 0 {
            return 0;
        }
        if keys[3 * j + 2] == 1 {
            let (v, bad) = query.parse_int(record[first..end]);
            if bad != 0 {
                return 0;
            }
            if decided == 0 {
                let held_v = vec.get(s.acc, last * stride_of(s.stride) + 2 + 2 * j);
                if v < held_v {
                    c = 0 - 1;
                } else if v > held_v {
                    c = 1;
                }
            }
        } else if decided == 0 {
            c = bytes.compare(record[first..end], key_bytes(s, last, j));
        }
        if decided == 0 {
            if keys[3 * j + 1] == 1 {
                c = 0 - c;
            }
            if c < 0 {
                decided = 1;
            } else if c > 0 {
                decided = 2;
            }
        }
        j = j + 1;
    }
    if decided == 1 {
        return 0;
    }
    return 1;
}

// One more row: 0 held; 1 `max_rows` would be passed; 2 `max_state` bytes would be; 3 a cell of an integer key is not an
// integer, 4 it does not fit 64 bits (`which` is the key). `scratch` is for unquoting.
pub fn add[&h, &d, &c](heap: &!h Heap, s: agg.Groups, record: &d [byte], cells: &c [int], scratch: buffer.Buffer, max_rows: int, max_state: int) -> [heap] (agg.Groups, buffer.Buffer, int, int) {
    let agg.Groups { index, acc, seen, keyb, dkey, held, pairs, stride, memo } = s;
    var d2 = keyb;
    var s2 = dkey;
    var r2 = acc;
    let count = pairs;
    let nk = stride;
    var scr = scratch;
    var status = 0;
    var which = 0;
    var size = 0;
    borrow d2 as &dr in {
        borrow s2 as &sr in {
            size = buffer.size(dr) + buffer.size(sr);
        }
    }
    let width = stride_of(nk);
    if count >= max_rows {
        status = 1;
    } else if size + len(record) + 8 * width * (count + 1) > max_state {
        status = 2;
    }
    if status != 0 {
        return (agg.Groups { index: index, acc: r2, seen: seen, keyb: d2, dkey: s2, held: held, pairs: count, stride: nk, memo: memo }, scr, status, which);
    }
    var at = 0;
    borrow d2 as &dr in {
        at = buffer.size(dr);
    }
    d2 = buffer.append(heap, d2, record);
    r2 = vec.push(heap, r2, at);
    r2 = vec.push(heap, r2, len(record));
    var j = 0;
    while j < nk && status == 0 {
        var column = 0;
        var as_int = 0;
        borrow memo as &mr in {
            column = contents(mr)[4 + 3 * j];
            as_int = contents(mr)[6 + 3 * j];
        }
        let first = cells[3 * column];
        let end = cells[3 * column + 1];
        let unquote = cells[3 * column + 2] == 1 && index_of_byte(record[first..end], byte_of(34)) >= 0;
        if as_int == 1 {
            var v = 0;
            var bad = 0;
            if unquote {
                borrow mut scr as &!sw in {
                    buffer.clear(sw);
                }
                scr = query.unquote(heap, scr, record[first..end]);
                borrow scr as &sr in {
                    let (x, y) = query.parse_int(buffer.bytes(sr));
                    v = x;
                    bad = y;
                }
            } else {
                let (x, y) = query.parse_int(record[first..end]);
                v = x;
                bad = y;
            }
            if bad == 1 {
                status = 3;
                which = j;
            } else if bad == 2 {
                status = 4;
                which = j;
            }
            r2 = vec.push(heap, r2, v);
            r2 = vec.push(heap, r2, 0);
        } else if unquote {
            var from = 0;
            borrow s2 as &sr in {
                from = buffer.size(sr);
            }
            s2 = query.unquote(heap, s2, record[first..end]);
            var to = 0;
            borrow s2 as &sr in {
                to = buffer.size(sr);
            }
            r2 = vec.push(heap, r2, 0 - from - 1);
            r2 = vec.push(heap, r2, to - from);
        } else {
            r2 = vec.push(heap, r2, at + first);
            r2 = vec.push(heap, r2, end - first);
        }
        j = j + 1;
    }
    var added = count + 1;
    if status != 0 {
        // A row that is refused is not kept: the read stops, and what is held is not used.
        added = count;
    }
    return (agg.Groups { index: index, acc: r2, seen: seen, keyb: d2, dkey: s2, held: held, pairs: added, stride: nk, memo: memo }, scr, status, which);
}

// Whether held row `x` is before held row `y` by the keys (equal keys: neither is before the other). `pre` is the first
// key's integer, or the first seven bytes of its text as one number, which settle most pairs.
fn before[&s, &k, &p, &q](s: &s agg.Groups, keys: &k [int], pre: &p [int], pre2: &q [int], x: int, y: int) -> [] bool {
    if pre[x] != pre[y] {
        if keys[1] == 1 {
            return pre[x] > pre[y];
        }
        return pre[x] < pre[y];
    }
    if pre2[x] != pre2[y] {
        if keys[1] == 1 {
            return pre2[x] > pre2[y];
        }
        return pre2[x] < pre2[y];
    }
    let stride = stride_of(s.stride);
    var j = 0;
    while j < s.stride {
        var c = 0;
        if keys[3 * j + 2] == 1 {
            let vx = vec.get(s.acc, x * stride + 2 + 2 * j);
            let vy = vec.get(s.acc, y * stride + 2 + 2 * j);
            if vx < vy {
                c = 0 - 1;
            } else if vx > vy {
                c = 1;
            }
        } else {
            c = bytes.compare(key_bytes(s, x, j), key_bytes(s, y, j));
        }
        if c != 0 {
            if keys[3 * j + 1] == 1 {
                return c > 0;
            }
            return c < 0;
        }
        j = j + 1;
    }
    return false;
}

fn prefix_of[&k](key: &k [byte], from: int) -> [] int {
    var v = 0;
    var i = from;
    while i < from + 7 {
        v = v * 256;
        if i < len(key) {
            v = v + int_of(key[i]);
        }
        i = i + 1;
    }
    return v;
}

// The held rows' numbers (their places in the index) in order: `order[0..count]`, by a bottom-up merge sort through a
// spare, O(n log n).
fn sort_into[&s, &k, &o, &t, &p, &q](s: &s agg.Groups, keys: &k [int], order: &!o [int], spare: &!t [int], pre: &!p [int], pre2: &!q [int]) -> [] int {
    let n = s.pairs;
    let stride = stride_of(s.stride);
    var i = 0;
    while i < n {
        order[i] = i;
        if s.stride > 0 {
            if keys[2] == 1 {
                pre[i] = vec.get(s.acc, i * stride + 2);
            } else {
                let kb = key_bytes(s, i, 0);
                pre[i] = prefix_of(kb, 0);
                pre2[i] = prefix_of(kb, 7);
            }
        }
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
            var w = lo;
            while w < hi {
                if p < mid && (q >= hi || !before(s, keys, pre, pre2, order[q], order[p])) {
                    spare[w] = order[p];
                    p = p + 1;
                } else {
                    spare[w] = order[q];
                    q = q + 1;
                }
                w = w + 1;
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

// The held rows' places, in order, as a box the caller frees.
pub fn order[&h, &s](heap: &!h Heap, s: &s agg.Groups) -> [heap] Box[[int]] {
    let keys = contents(s.memo)[4..4 + 3 * s.stride];
    let n = s.pairs;
    let ord = box_slice(heap, n + 1, 0);
    let spare = box_slice(heap, n + 1, 0);
    let pre = box_slice(heap, n + 1, 0);
    let pre2 = box_slice(heap, n + 1, 0);
    borrow mut ord as &!ow in {
        borrow mut spare as &!sw in {
            borrow mut pre as &!pw in {
                borrow mut pre2 as &!qw in {
                    sort_into(s, keys, contents(ow), contents(sw), contents(pw), contents(qw));
                }
            }
        }
    }
    unbox_slice(heap, spare);
    unbox_slice(heap, pre);
    unbox_slice(heap, pre2);
    return ord;
}

// Sort what is held and keep the first `cap`: the rows of the answer that are wanted, in order, and the last of them
// is what `reject` compares with from now on.
pub fn cut_back[&h](heap: &!h Heap, old: agg.Groups) -> [heap] agg.Groups {
    var nk = 0;
    var count = 0;
    var cap = 0;
    borrow old as &o0 in {
        nk = o0.stride;
        count = o0.pairs;
        cap = contents(o0.memo)[0];
    }
    let width = stride_of(nk);
    var fresh_data = buffer.empty(heap, 4096);
    var fresh_side = buffer.empty(heap, 64);
    var fresh_rows = vec.empty(heap, 1024, 0);
    borrow old as &or in {
        let keys = contents(or.memo)[4..4 + 3 * or.stride];
        let ord = order(heap, or);
        var i = 0;
        while i < cap && i < count {
            var x = 0;
            borrow ord as &ordr in {
                x = contents(ordr)[i];
            }
            var at = 0;
            borrow fresh_data as &dr in {
                at = buffer.size(dr);
            }
            let old_off = vec.get(or.acc, x * width);
            fresh_data = buffer.append(heap, fresh_data, record_of(or, x));
            fresh_rows = vec.push(heap, fresh_rows, at);
            fresh_rows = vec.push(heap, fresh_rows, vec.get(or.acc, x * width + 1));
            var j = 0;
            while j < nk {
                let a = vec.get(or.acc, x * width + 2 + 2 * j);
                let b = vec.get(or.acc, x * width + 3 + 2 * j);
                if keys[3 * j + 2] == 1 {
                    fresh_rows = vec.push(heap, fresh_rows, a);
                    fresh_rows = vec.push(heap, fresh_rows, b);
                } else if a >= 0 {
                    fresh_rows = vec.push(heap, fresh_rows, at + a - old_off);
                    fresh_rows = vec.push(heap, fresh_rows, b);
                } else {
                    var from = 0;
                    borrow fresh_side as &sr in {
                        from = buffer.size(sr);
                    }
                    fresh_side = buffer.append(heap, fresh_side, key_bytes(or, x, j));
                    fresh_rows = vec.push(heap, fresh_rows, 0 - from - 1);
                    fresh_rows = vec.push(heap, fresh_rows, b);
                }
                j = j + 1;
            }
            i = i + 1;
        }
        unbox_slice(heap, ord);
    }
    var kept_count = count;
    if kept_count > cap {
        kept_count = cap;
    }
    let agg.Groups { index, acc, seen, keyb, dkey, held, pairs, stride, memo } = old;
    buffer.drop(heap, keyb);
    buffer.drop(heap, dkey);
    vec.drop(heap, acc);
    borrow mut memo as &!mw in {
        contents(mw)[1] = 1;
    }
    return agg.Groups { index: index, acc: fresh_rows, seen: seen, keyb: fresh_data, dkey: fresh_side, held: held, pairs: kept_count, stride: stride, memo: memo };
}

// A row, in place: dropped when only the first rows are wanted and it is not before the last of them, else held, in
// the room that the buffers have. Answers 0 when that is done, 1 when the row has to take the way that can grow the
// buffers, cut them back, unquote a key or refuse (`engine.order_row`), in which case nothing was changed.
pub fn hook[&g, &d, &c](g: &!g agg.Groups, record: &d [byte], cells: &c [int]) -> [] int {
    var rejected = 0;
    rejected = reject(g, record, cells);
    if rejected == 1 {
        return 0;
    }
    let nk = g.stride;
    let width = stride_of(nk);
    let count = g.pairs;
    let cap = contents(g.memo)[0];
    if count >= contents(g.memo)[2] || cap > 0 && count + 1 >= 2 * cap + 1 {
        return 1;
    }
    let used = g.acc.used;
    let table = contents(g.acc.held);
    if len(table) - used < width || len(buffer.room(g.keyb)) < len(record) {
        return 1;
    }
    if buffer.size(g.keyb) + buffer.size(g.dkey) + len(record) + 8 * width * (count + 1) > contents(g.memo)[3] {
        return 1;
    }
    // The keys: an integer is read, a text is where it lies; a cell that needs unquoting, or is not an integer, is for `add`.
    var j = 0;
    let at = buffer.size(g.keyb);
    while j < nk {
        let column = contents(g.memo)[4 + 3 * j];
        let first = cells[3 * column];
        let end = cells[3 * column + 1];
        if cells[3 * column + 2] == 1 && index_of_byte(record[first..end], byte_of(34)) >= 0 {
            return 1;
        }
        if contents(g.memo)[6 + 3 * j] == 1 {
            let (v, bad) = query.parse_int(record[first..end]);
            if bad != 0 {
                return 1;
            }
            table[used + 2 + 2 * j] = v;
            table[used + 3 + 2 * j] = 0;
        } else {
            table[used + 2 + 2 * j] = at + first;
            table[used + 3 + 2 * j] = end - first;
        }
        j = j + 1;
    }
    table[used] = at;
    table[used + 1] = len(record);
    let room = buffer.room(g.keyb);
    copy_into(room[0..len(record)], record);
    buffer.filled(g.keyb, len(record));
    g.acc.used = used + width;
    g.pairs = count + 1;
    return 0;
}
