edition 5;

module plan;

// The columns `--select` names, and what is said when one is not there.
//
// The list is NAMES: names separated by commas. A backslash escapes the next
// byte, which has to be a comma, a backslash or a `#` (`\,` is a comma in a
// name, `\\` a backslash, `\#` a `#`); a backslash before anything else, or at
// the end, is not a list. A name is exact and case-sensitive. A name that is
// `#` and digits is the column at that 1-based position instead; a header that
// really is `#3` is written `\#3`, and `#id` needs no escape, because a
// position is `#` and digits and nothing else.
//
// An empty list is one name, the empty one (a header may have one).

import std.buffer;
import std.bytes;
import std.json;
import std.map;
import std.vec;
import toolbox.fail;
import query;
import toolbox.text;

// The ceiling on how many columns one `--select` names.
pub fn most_names() -> [] int {
    return 4096;
}

// NAMES as its names: their bytes one after another, where each ends, and for
// each the 1-based position it names or -1 for a name. Answers whether it was
// a list.
pub fn parse[&h, &t](heap: &!h Heap, given: &t [byte]) -> [heap] (buffer.Buffer, vec.Vec[int], vec.Vec[int], bool) {
    var toks = buffer.empty(heap, len(given) + 1);
    var ends = vec.empty(heap, 8, 0);
    var kinds = vec.empty(heap, 8, 0);
    var ok = true;
    var i = 0;
    var from = 0;
    var hash = false;
    var done = false;
    while !done && ok {
        var c = -1;
        if i < len(given) {
            c = int_of(given[i]);
        }
        if c == '\\' {
            if i + 1 < len(given) && (int_of(given[i + 1]) == ',' || int_of(given[i + 1]) == '\\' || int_of(given[i + 1]) == '#') {
                toks = buffer.push(heap, toks, given[i + 1]);
                i = i + 2;
            } else {
                ok = false;
            }
        } else if c == ',' || c < 0 {
            // A name ends: a position when it began with an unescaped `#` and
            // the rest is digits.
            var size = 0;
            var position = -1;
            borrow toks as &r in {
                size = buffer.size(r);
                let name = buffer.bytes(r)[from..size];
                if hash && len(name) >= 2 && len(name) <= 10 {
                    var k = 1;
                    var n = 0;
                    var digits = true;
                    while k < len(name) {
                        if !bytes.is_digit(int_of(name[k])) {
                            digits = false;
                        } else {
                            n = n * 10 + bytes.digit_of(int_of(name[k]));
                        }
                        k = k + 1;
                    }
                    if digits {
                        position = n;
                    }
                }
            }
            ends = vec.push(heap, ends, size);
            kinds = vec.push(heap, kinds, position);
            from = size;
            hash = false;
            if c < 0 {
                done = true;
            }
            i = i + 1;
        } else {
            // (an unescaped `#` after the first byte makes the rest not digits,
            // so only a first one can make a position)
            if c == '#' {
                hash = true;
            }
            toks = buffer.push(heap, toks, byte_of(c));
            i = i + 1;
        }
    }
    return (toks, ends, kinds, ok);
}

// How the names fared against a header.
pub struct Resolved {
    // 0 every name found, 1 a name that is not a column, 2 a name that is
    // more than one column.
    status: int,
    // The name that failed (0-based), when status is not 0.
    at: int,
}

// The column of each name: `columns` is filled with 0-based positions.
pub fn resolve[&h, &n, &e, &t, &k, &j, &c](heap: &!h Heap, header: &n buffer.Buffer, header_ends: &e vec.Vec[int], toks: &t buffer.Buffer, ends: &k vec.Vec[int], kinds: &j vec.Vec[int], columns: &!c [int]) -> [heap] Resolved {
    var names = map.empty(heap, 64, 0, 0x5eed);
    let width = vec.size(header_ends);
    var i = 0;
    var from = 0;
    while i < width {
        let to = vec.get(header_ends, i);
        let key = buffer.bytes(header)[from..to];
        var found = -1;
        borrow names as &r in {
            found = map.find(r, key);
        }
        if found >= 0 {
            // The same name twice: it names no one column.
            borrow mut names as &!w in {
                map.set_value_at(w, found, 0 - 2);
            }
        } else {
            names = map.put(heap, names, key, i);
        }
        from = to;
        i = i + 1;
    }
    var status = 0;
    var at = 0;
    var k = 0;
    var begin = 0;
    while k < vec.size(ends) && status == 0 {
        let to = vec.get(ends, k);
        let position = vec.get(kinds, k);
        var column = -1;
        if position >= 0 {
            if position >= 1 && position <= width {
                column = position - 1;
            }
        } else {
            borrow names as &r in {
                column = map.get(r, buffer.bytes(toks)[begin..to], -1);
            }
        }
        if column == -2 {
            status = 2;
            at = k;
        } else if column < 0 {
            status = 1;
            at = k;
        } else {
            columns[k] = column;
        }
        begin = to;
        k = k + 1;
    }
    map.drop(heap, names);
    return Resolved { status: status, at: at };
}

// Whether `a` and `b` are the same ASCII letters, ignoring case.
fn same_letters[&a, &b](a: &a [byte], b: &b [byte]) -> [] bool {
    if len(a) != len(b) {
        return false;
    }
    var i = 0;
    while i < len(a) {
        if bytes.to_lower(int_of(a[i])) != bytes.to_lower(int_of(b[i])) {
            return false;
        }
        i = i + 1;
    }
    return true;
}

// The list with name `k` replaced by `name`, written so that `parse` reads it
// back: a comma, a backslash and a leading `#` escaped.
pub fn rewritten[&h, &t, &e, &k, &n](heap: &!h Heap, toks: &t buffer.Buffer, ends: &e vec.Vec[int], kinds: &k vec.Vec[int], replaced: int, upto: int, name: &n [byte]) -> [heap] buffer.Buffer {
    var out = buffer.empty(heap, 64);
    var i = 0;
    var from = 0;
    while i < upto {
        let to = vec.get(ends, i);
        if i > 0 {
            out = buffer.push(heap, out, byte_of(','));
        }
        if i == replaced {
            out = escaped(heap, out, name);
        } else if vec.get(kinds, i) < 0 {
            out = escaped(heap, out, buffer.bytes(toks)[from..to]);
        } else {
            out = buffer.push(heap, out, byte_of('#'));
            out = text_nat(heap, out, vec.get(kinds, i));
        }
        from = to;
        i = i + 1;
    }
    return out;
}

// A name as `parse` reads it back: a comma, a backslash and a leading `#`
// escaped.
fn escaped[&h, &d](heap: &!h Heap, out: buffer.Buffer, data: &d [byte]) -> [heap] buffer.Buffer {
    var o = out;
    var j = 0;
    while j < len(data) {
        let c = int_of(data[j]);
        if c == ',' || c == '\\' || c == '#' && j == 0 {
            o = buffer.push(heap, o, byte_of('\\'));
        }
        o = buffer.push(heap, o, byte_of(c));
        j = j + 1;
    }
    return o;
}

fn text_nat[&h](heap: &!h Heap, out: buffer.Buffer, n: int) -> [heap] buffer.Buffer {
    return buffer.push_nat(heap, out, n);
}

// The refusal for a name that is not a column (status 1) or is more than one
// (status 2): `rule` is its tag. The detail lists the header (the first 50
// names) and, when the name is a column but for its case, the repair is a
// choice of the invocation with that column's name written as the header has
// it. `at_arg` is where NAMES is in argv (-1 when it is not an argument of its
// own, and then there is no repair to offer).
pub fn refusal[&h, &g, &n, &e, &t, &k, &j, &s](heap: &!h Heap, errs: fail.Errors, extra: &static [byte], rule: &static [byte], message: &static [byte], hint: &static [byte], args: &g Args, at_arg: int, header: &n buffer.Buffer, header_ends: &e vec.Vec[int], toks: &t buffer.Buffer, ends: &k vec.Vec[int], kinds: &j vec.Vec[int], bad: int, upto: int, flag: &static [byte], shown: &s [byte]) -> [heap, args] fail.Errors {
    var begin = 0;
    if bad > 0 {
        begin = vec.get(ends, bad - 1);
    }
    let wanted = buffer.bytes(toks)[begin..vec.get(ends, bad)];
    var w = fail.open_in(heap, extra, rule, message, hint);
    // The headers that differ from the name only in case.
    var options = vec.empty(heap, 4, 0);
    var kept = 0;
    var i = 0;
    var from = 0;
    while i < vec.size(header_ends) && kept < 5 {
        let to = vec.get(header_ends, i);
        if same_letters(buffer.bytes(header)[from..to], wanted) && vec.get(kinds, bad) < 0 {
            options = vec.push(heap, options, i);
            kept = kept + 1;
        }
        from = to;
        i = i + 1;
    }
    if at_arg >= 0 && kept > 0 && bytes.equal(rule, "select.unknown-column") {
        var o = fail.choose_open(heap, w);
        var k2 = 0;
        while k2 < kept {
            var column = 0;
            borrow options as &or in {
                column = vec.get(or, k2);
            }
            var start = 0;
            if column > 0 {
                start = vec.get(header_ends, column - 1);
            }
            let spelled = buffer.bytes(header)[start..vec.get(header_ends, column)];
            let list = rewritten(heap, toks, ends, kinds, bad, upto, spelled);
            borrow list as &lr in {
                o = fail.choose_option_replacing(heap, o, args, at_arg, buffer.bytes(lr));
            }
            buffer.drop(heap, list);
            k2 = k2 + 1;
        }
        w = fail.choose_close(heap, o);
    } else {
        w = fail.repair_none(heap, w, "which column was meant is not known; detail.available lists the header");
    }
    vec.drop(heap, options);
    w = fail.detail_open(heap, w);
    w = fail.detail_text(heap, w, "path", shown);
    w = fail.detail_str(heap, w, "flag", flag);
    w = fail.detail_text(heap, w, "name", wanted);
    w = fail.detail_int(heap, w, "columns", vec.size(header_ends));
    w = json.put_key(heap, w, "available");
    w = json.begin_array(heap, w);
    var j = 0;
    var start = 0;
    while j < vec.size(header_ends) && j < 50 {
        let to = vec.get(header_ends, j);
        w = text.put(heap, w, buffer.bytes(header)[start..to]);
        start = to;
        j = j + 1;
    }
    w = json.end_array(heap, w);
    w = fail.detail_bool(heap, w, "available_truncated", vec.size(header_ends) > 50);
    return fail.add(heap, errs, w);
}

// Every name of a list `parse` read, added to the plan as it said.
pub fn fill[&h, &t, &e, &k](heap: &!h Heap, tree: query.Query, toks: &t buffer.Buffer, ends: &e vec.Vec[int], kinds: &k vec.Vec[int]) -> [heap] query.Query {
    var q = tree;
    var i = 0;
    var from = 0;
    while i < vec.size(ends) {
        let to = vec.get(ends, i);
        let (q2, at) = query.add_name(heap, q, buffer.bytes(toks)[from..to], vec.get(kinds, i));
        q = q2;
        from = to;
        i = i + 1;
    }
    return q;
}
