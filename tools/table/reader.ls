edition 5;

module reader;

// The RFC 4180 reader's pieces: what is true of one line of a record, the
// names of a header, and the fields of a whole record. Nothing here reads a
// file or writes anything; `table.ls` drives it a line at a time.
//
// Three functions, three questions about the same grammar, kept in step by
// the differential test against Python's csv module:
//
// * `scan` -- does this line end its record, and how many delimiters did the
//   record hold outside quotes (a count of fields without finding them);
// * `fields` -- where each field of a record that is whole in one slice is;
// * `split_header` -- the names of the header, unescaped.

import std.buffer;
import std.bytes;
import std.vec;

// One physical line of a record, from the state the previous line left.
// `quoted` is whether a quoted field is open, `seps` the delimiters of the
// record outside quotes so far. Answers the same two at the end of the line
// and whether a quote closed and was followed by something it may not be.
//
// The line is walked by its quotes, not its bytes: between two quotes the
// delimiters are counted with `count_byte`, so a row whose one quoted field
// is the only quote costs three searches, not one step per byte.
pub fn scan[&l](line: &l [byte], delim: int, quoted: bool, seps: int) -> [] (bool, int, bool) {
    let end = len(line);
    var p = 0;
    var inside = quoted;
    var n = seps;
    var bad = false;
    while p < end && !bad {
        let found = index_of_byte(line[p..end], byte_of(34));
        if !inside {
            var q = end;
            if found >= 0 {
                q = p + found;
            }
            n = n + bytes.count_byte(line[p..q], delim);
            if q == end {
                p = end;
            } else if q == 0 || int_of(line[q - 1]) == delim {
                // A quote at the start of a field opens a quoted one.
                inside = true;
                p = q + 1;
            } else {
                // A quote in the middle of an unquoted field is text.
                p = q + 1;
            }
        } else if found < 0 {
            p = end;
        } else {
            let q = p + found;
            if q + 1 < end && int_of(line[q + 1]) == 34 {
                // A doubled quote is one quote of text.
                p = q + 2;
            } else {
                inside = false;
                if q + 1 == end {
                    p = end;
                } else if int_of(line[q + 1]) == delim {
                    n = n + 1;
                    p = q + 2;
                } else {
                    bad = true;
                }
            }
        }
    }
    return (inside, n, bad);
}

// The names in one whole header record `raw` (its lines joined by LF, the last
// CR dropped): their bytes, one after another, and where each one ends. `scan`
// has already said the record is well formed.
pub fn split_header[&h, &r](heap: &!h Heap, raw: &r [byte], delim: int) -> [heap] (buffer.Buffer, vec.Vec[int]) {
    var n = len(raw);
    if n > 0 && int_of(raw[n - 1]) == 13 {
        n = n - 1;
    }
    var names = buffer.empty(heap, n + 1);
    var ends = vec.empty(heap, 8, 0);
    var i = 0;
    var inside = false;
    var start = true;
    while i < n {
        let c = int_of(raw[i]);
        if inside {
            if c == 34 {
                if i + 1 < n && int_of(raw[i + 1]) == 34 {
                    names = buffer.push(heap, names, byte_of(34));
                    i = i + 2;
                } else {
                    inside = false;
                    i = i + 1;
                }
            } else {
                names = buffer.push(heap, names, byte_of(c));
                i = i + 1;
            }
        } else if c == delim {
            var size = 0;
            borrow names as &nr in {
                size = buffer.size(nr);
            }
            ends = vec.push(heap, ends, size);
            start = true;
            i = i + 1;
        } else if c == 34 && start {
            inside = true;
            start = false;
            i = i + 1;
        } else {
            names = buffer.push(heap, names, byte_of(c));
            start = false;
            i = i + 1;
        }
    }
    var size = 0;
    borrow names as &nr in {
        size = buffer.size(nr);
    }
    ends = vec.push(heap, ends, size);
    return (names, ends);
}

// Where the byte `b` first is in `line[from..end]` (an index into `line`), or -1. Short fields, most of
// them, are walked: a call to `memchr` costs more than the bytes it would skip. One that is still going
// after 24 bytes is left to `memchr`.
fn seek[&l](line: &l [byte], from: int, end: int, b: int) -> [] int {
    var q = from;
    var stop = end;
    if end - from > 24 {
        stop = from + 24;
    }
    while q < stop && int_of(line[q]) != b {
        q = q + 1;
    }
    if q < stop {
        return q;
    }
    if q == end {
        return 0 - 1;
    }
    let more = index_of_byte(line[q..end], byte_of(b));
    if more < 0 {
        return 0 - 1;
    }
    return q + more;
}

// The fields of the record `line`, found in one pass: for field `n` its
// `cells[3n]` start, `cells[3n + 1]` end and `cells[3n + 2]` 1 when it was
// quoted (start and end then exclude the quotes, and a doubled quote is still
// doubled in between). Only the first `room` fields are stored, all are
// counted. Answers (fields, a quote still open at the end, a bad quote), the
// same two refusals as `scan`.
//
// A field is found with one `memchr` for its delimiter, or for the quote that
// closes it, so a record costs a search per field and not a step per byte.
pub fn fields[&l, &c](line: &l [byte], delim: int, cells: &!c [int], room: int) -> [] (int, bool, bool) {
    let end = len(line);
    var p = 0;
    var n = 0;
    var inside = false;
    var bad = false;
    var going = true;
    while going {
        var first = p;
        var last = end;
        var quoted = 0;
        var next = end + 1;
        if p < end && int_of(line[p]) == 34 {
            var q = p + 1;
            var closed = false;
            while !closed && !inside {
                let at = seek(line, q, end, 34);
                if at < 0 {
                    inside = true;
                } else if at + 1 < end && int_of(line[at + 1]) == 34 {
                    q = at + 2;
                } else {
                    closed = true;
                    first = p + 1;
                    last = at;
                    quoted = 1;
                    if at + 1 == end {
                        next = end + 1;
                    } else if int_of(line[at + 1]) == delim {
                        next = at + 2;
                    } else {
                        bad = true;
                    }
                }
            }
        } else {
            let q = seek(line, p, end, delim);
            if q >= 0 {
                last = q;
                next = q + 1;
            }
        }
        if inside || bad {
            going = false;
        } else {
            if n < room {
                cells[3 * n] = first;
                cells[3 * n + 1] = last;
                cells[3 * n + 2] = quoted;
            }
            n = n + 1;
            if next > end {
                going = false;
            } else {
                p = next;
            }
        }
    }
    return (n, inside, bad);
}
