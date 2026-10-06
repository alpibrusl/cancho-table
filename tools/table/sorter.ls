edition 5;

module sorter;

// `--order-by`: the rows of the answer, held and sorted (docs/sort.md).
//
// A row is kept as the record was read, in `data`; the index `rows` has `stride` integers a row: where the
// record begins in `data`, its length, its number in the file, then for each key two integers: for an
// integer key its value and 0; for a text key where its bytes are and how long they are, in `data` (an
// offset of 0 or more: the cell had no doubled quote) or in `side` (an offset `-1 - at`: the cell was quoted
// with doubled quotes, and what it says is kept there unquoted).
//
// The order of two rows is their keys, each ascending or descending, then the rows' numbers in the file,
// so it is total, and the sort is stable by construction.
//
// `keys` (the plan's, built once) has three integers a key: the column, 1 for descending, 1 for an integer.
//
// With `cap` above 0 only the `cap` first rows are wanted: once `2 * cap + 1` are held they are sorted and
// cut to `cap`, and a row that is not before the last of those is not kept at all (`reject`): memory is
// O(cap), whatever the file.

import std.buffer;
import std.bytes;
import std.vec;
import query;

pub res struct Sorter {
    data: buffer.Buffer,
    side: buffer.Buffer,
    rows: vec.Vec[int],
    nk: int,
    count: int,
    seq: int,
    cap: int,
    cut: int,
}

pub fn start[&h](heap: &!h Heap, nk: int, cap: int) -> [heap] Sorter {
    return Sorter { data: buffer.empty(heap, 4096), side: buffer.empty(heap, 64), rows: vec.empty(heap, 1024, 0), nk: nk, count: 0, seq: 0, cap: cap, cut: 0 };
}

pub fn drop[&h](heap: &!h Heap, s: Sorter) -> [heap] int {
    let Sorter { data, side, rows, nk, count, seq, cap, cut } = s;
    buffer.drop(heap, data);
    buffer.drop(heap, side);
    vec.drop(heap, rows);
    return 0;
}

pub fn held[&s](s: &s Sorter) -> [] int {
    return s.count;
}

fn stride_of(nk: int) -> [] int {
    return 3 + 2 * nk;
}

// The bytes of a held row's record.
pub fn record_of[&s](s: &s Sorter, row: int) -> [] &s [byte] {
    let at = row * stride_of(s.nk);
    let off = vec.get(s.rows, at);
    return buffer.bytes(s.data)[off..off + vec.get(s.rows, at + 1)];
}

// The bytes of key `j` of a held row.
fn key_bytes[&s](s: &s Sorter, row: int, j: int) -> [] &s [byte] {
    let at = row * stride_of(s.nk) + 3 + 2 * j;
    let off = vec.get(s.rows, at);
    let n = vec.get(s.rows, at + 1);
    if off >= 0 {
        return buffer.bytes(s.data)[off..off + n];
    }
    let o = 0 - off - 1;
    return buffer.bytes(s.side)[o..o + n];
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
pub fn reject[&s, &k, &d, &c](s: &s Sorter, keys: &k [int], record: &d [byte], cells: &c [int]) -> [] int {
    if s.cut == 0 {
        return 0;
    }
    let last = s.cap - 1;
    // 0 not decided yet, 1 keep it, 2 drop it. Every integer key is read either way: a cell that is not an integer is a
    // refusal whether the row would have been kept or not (the answer must not depend on the other rows).
    var decided = 0;
    var j = 0;
    while j < s.nk {
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
                let held_v = vec.get(s.rows, last * stride_of(s.nk) + 3 + 2 * j);
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
pub fn add[&h, &k, &d, &c](heap: &!h Heap, s: Sorter, keys: &k [int], record: &d [byte], cells: &c [int], scratch: buffer.Buffer, max_rows: int, max_state: int) -> [heap] (Sorter, buffer.Buffer, int, int) {
    let Sorter { data, side, rows, nk, count, seq, cap, cut } = s;
    var d2 = data;
    var s2 = side;
    var r2 = rows;
    var scr = scratch;
    var status = 0;
    var which = 0;
    var size = 0;
    borrow d2 as &dr in {
        borrow s2 as &sr in {
            size = buffer.size(dr) + buffer.size(sr);
        }
    }
    let stride = stride_of(nk);
    if count >= max_rows {
        status = 1;
    } else if size + len(record) + 8 * stride * (count + 1) > max_state {
        status = 2;
    }
    if status != 0 {
        return (Sorter { data: d2, side: s2, rows: r2, nk: nk, count: count, seq: seq, cap: cap, cut: cut }, scr, status, which);
    }
    var at = 0;
    borrow d2 as &dr in {
        at = buffer.size(dr);
    }
    d2 = buffer.append(heap, d2, record);
    r2 = vec.push(heap, r2, at);
    r2 = vec.push(heap, r2, len(record));
    r2 = vec.push(heap, r2, seq);
    var j = 0;
    while j < nk && status == 0 {
        let column = keys[3 * j];
        let first = cells[3 * column];
        let end = cells[3 * column + 1];
        let unquote = cells[3 * column + 2] == 1 && index_of_byte(record[first..end], byte_of(34)) >= 0;
        if keys[3 * j + 2] == 1 {
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
    return (Sorter { data: d2, side: s2, rows: r2, nk: nk, count: added, seq: seq + 1, cap: cap, cut: cut }, scr, status, which);
}

// Whether held row `x` is before held row `y`: by the keys, then by the rows' numbers in the file. `pre` is the first
// key's integer, or the first seven bytes of its text as one number, which settle most pairs.
fn before[&s, &k, &p](s: &s Sorter, keys: &k [int], pre: &p [int], x: int, y: int) -> [] bool {
    if pre[x] != pre[y] {
        if keys[1] == 1 {
            return pre[x] > pre[y];
        }
        return pre[x] < pre[y];
    }
    let stride = stride_of(s.nk);
    var j = 0;
    while j < s.nk {
        var c = 0;
        if keys[3 * j + 2] == 1 {
            let vx = vec.get(s.rows, x * stride + 3 + 2 * j);
            let vy = vec.get(s.rows, y * stride + 3 + 2 * j);
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
    return vec.get(s.rows, x * stride + 2) < vec.get(s.rows, y * stride + 2);
}

fn prefix_of[&k](key: &k [byte]) -> [] int {
    var v = 0;
    var i = 0;
    while i < 7 {
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
fn sort_into[&s, &k, &o, &t, &p](s: &s Sorter, keys: &k [int], order: &!o [int], spare: &!t [int], pre: &!p [int]) -> [] int {
    let n = s.count;
    let stride = stride_of(s.nk);
    var i = 0;
    while i < n {
        order[i] = i;
        if s.nk > 0 {
            if keys[2] == 1 {
                pre[i] = vec.get(s.rows, i * stride + 3);
            } else {
                pre[i] = prefix_of(key_bytes(s, i, 0));
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
                if p < mid && (q >= hi || !before(s, keys, pre, order[q], order[p])) {
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
pub fn order[&h, &s, &k](heap: &!h Heap, s: &s Sorter, keys: &k [int]) -> [heap] Box[[int]] {
    let n = s.count;
    let ord = box_slice(heap, n + 1, 0);
    let spare = box_slice(heap, n + 1, 0);
    let pre = box_slice(heap, n + 1, 0);
    borrow mut ord as &!ow in {
        borrow mut spare as &!sw in {
            borrow mut pre as &!pw in {
                sort_into(s, keys, contents(ow), contents(sw), contents(pw));
            }
        }
    }
    unbox_slice(heap, spare);
    unbox_slice(heap, pre);
    return ord;
}

// Sort what is held and keep the first `cap`: the rows of the answer that are wanted, in order, and the last of them
// is what `reject` compares with from now on.
pub fn cut_back[&h, &k](heap: &!h Heap, s: Sorter, keys: &k [int]) -> [heap] Sorter {
    let Sorter { data, side, rows, nk, count, seq, cap, cut } = s;
    let old = Sorter { data: data, side: side, rows: rows, nk: nk, count: count, seq: seq, cap: cap, cut: cut };
    let stride = stride_of(nk);
    var fresh_data = buffer.empty(heap, 4096);
    var fresh_side = buffer.empty(heap, 64);
    var fresh_rows = vec.empty(heap, 1024, 0);
    borrow old as &or in {
        let ord = order(heap, or, keys);
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
            let old_off = vec.get(or.rows, x * stride);
            fresh_data = buffer.append(heap, fresh_data, record_of(or, x));
            fresh_rows = vec.push(heap, fresh_rows, at);
            fresh_rows = vec.push(heap, fresh_rows, vec.get(or.rows, x * stride + 1));
            fresh_rows = vec.push(heap, fresh_rows, vec.get(or.rows, x * stride + 2));
            var j = 0;
            while j < nk {
                let a = vec.get(or.rows, x * stride + 3 + 2 * j);
                let b = vec.get(or.rows, x * stride + 4 + 2 * j);
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
    drop(heap, old);
    return Sorter { data: fresh_data, side: fresh_side, rows: fresh_rows, nk: nk, count: kept_count, seq: seq, cap: cap, cut: 1 };
}
