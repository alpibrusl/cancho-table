edition 5;

module writer;

// What `table` writes for a selected field: CSV (RFC 4180, minimal quoting) and
// JSON (`text_or_bytes`, as the other tools). Each function appends to a
// buffer and hands it back; nothing here writes to standard output.
//
// A field comes either from a record's bytes with its bounds (`start`, `end`,
// and whether it was quoted, from `reader.fields`), in which case a quoted
// field's bytes are still in their doubled form, or as plain bytes (a header
// name, already unescaped).

import std.buffer;
import toolbox.text;

// Whether `data` holds the byte `b`.
fn has[&d](data: &d [byte], b: int) -> [] bool {
    return index_of_byte(data, byte_of(b)) >= 0;
}

// Whether `data` has to be quoted to be one CSV field: it holds a quote or a CR, and, when `all` is 1, the
// delimiter or an LF. A short value is walked (one pass for all the bytes asked about); a long one is
// searched with `memchr`, a byte at a time of the set.
fn must_quote[&d](data: &d [byte], delim: int, all: int) -> [] bool {
    let n = len(data);
    if n <= 32 {
        var i = 0;
        while i < n {
            let c = int_of(data[i]);
            if c == 34 || c == 13 {
                return true;
            }
            if all == 1 && (c == delim || c == 10) {
                return true;
            }
            i = i + 1;
        }
        return false;
    }
    if has(data, 34) || has(data, 13) {
        return true;
    }
    return all == 1 && (has(data, delim) || has(data, 10));
}

// `data` with every quote doubled, appended.
fn doubled[&h, &d](heap: &!h Heap, out: buffer.Buffer, data: &d [byte]) -> [heap] buffer.Buffer {
    var o = out;
    var p = 0;
    while p < len(data) {
        let found = index_of_byte(data[p..len(data)], byte_of(34));
        if found < 0 {
            o = buffer.append(heap, o, data[p..len(data)]);
            p = len(data);
        } else {
            o = buffer.append(heap, o, data[p..p + found + 1]);
            o = buffer.push(heap, o, byte_of(34));
            p = p + found + 1;
        }
    }
    return o;
}

// Plain bytes as one CSV field: quoted when they hold the delimiter, a quote,
// a CR or an LF.
pub fn csv_value[&h, &d](heap: &!h Heap, out: buffer.Buffer, data: &d [byte], delim: int) -> [heap] buffer.Buffer {
    if !must_quote(data, delim, 1) {
        return buffer.append(heap, out, data);
    }
    var o = buffer.push(heap, out, byte_of(34));
    o = doubled(heap, o, data);
    return buffer.push(heap, o, byte_of(34));
}

// The field `record[first..last]` as one CSV field. An unquoted field holds
// no delimiter and no LF, so it is quoted only for a quote or a CR; a quoted
// one is quoted again only when it has to be, and its doubled quotes are
// already what the output needs.
pub fn csv_cell[&h, &d](heap: &!h Heap, out: buffer.Buffer, record: &d [byte], first: int, last: int, quoted: int, delim: int) -> [heap] buffer.Buffer {
    let data = record[first..last];
    if quoted == 0 {
        if !must_quote(data, delim, 0) {
            return buffer.append(heap, out, data);
        }
        var o = buffer.push(heap, out, byte_of(34));
        o = doubled(heap, o, data);
        return buffer.push(heap, o, byte_of(34));
    }
    if !must_quote(data, delim, 1) {
        return buffer.append(heap, out, data);
    }
    var o = buffer.push(heap, out, byte_of(34));
    o = buffer.append(heap, o, data);
    return buffer.push(heap, o, byte_of(34));
}

// The same field as a JSON `text_or_bytes` value: a quoted field's doubled
// quotes are undone first.
pub fn json_cell[&h, &d](heap: &!h Heap, out: buffer.Buffer, record: &d [byte], first: int, last: int, quoted: int) -> [heap] buffer.Buffer {
    let data = record[first..last];
    if quoted == 0 || !has(data, 34) {
        return text.append_json(heap, out, data);
    }
    var plain = buffer.empty(heap, len(data));
    var p = 0;
    while p < len(data) {
        let found = index_of_byte(data[p..len(data)], byte_of(34));
        if found < 0 {
            plain = buffer.append(heap, plain, data[p..len(data)]);
            p = len(data);
        } else {
            // `""` is one quote of text: keep the first, skip the second.
            plain = buffer.append(heap, plain, data[p..p + found + 1]);
            p = p + found + 2;
        }
    }
    var o = out;
    borrow plain as &r in {
        o = text.append_json(heap, o, buffer.bytes(r));
    }
    buffer.drop(heap, plain);
    return o;
}
