edition 5;

module scan;

// Reading a byte range of the data records: what a worker thread runs over its chunk
// of the file, and what the parent runs when a worker's answer cannot be used.
//
// The range is read with `file_pread` (the language has no `seek`, and a cursor would
// be shared by nothing anyway: every reader has a `File` of its own), by a copy of the
// contract's `lines.fill_file` that asks for the bytes at an offset. The loop that
// follows is the data-record half of the sequential read in table.ls, statement for
// statement: the same lines, the same records, the same `engine` functions for what is
// done with one. It has no header (that was read before), no BOM, no shape, no output
// stream (it returns when a block of csv is pending, and the caller writes it), and
// two ways of stopping the sequential read does not have: at a record that starts at
// or after `until`, and with a status that says why.
//
// `until` is where the next range begins. The records of a range are the ones that
// *start* before it; the last of them may run past it, and the range ends where the
// next record starts, which is what the caller needs to know, because it is the start
// of the next range if (and only if) the next range was guessed right.

import std.buffer;
import std.vec;
import agg;
import engine;
import query;
import reader;
import toolbox.lines;

// Where each parameter is in the array that carries them.
pub fn p_delim() -> [] int {
    return 0;
}

pub fn p_mode() -> [] int {
    return 1;
}

pub fn p_csv() -> [] int {
    return 2;
}

pub fn p_picked() -> [] int {
    return 3;
}

pub fn p_budget() -> [] int {
    return 4;
}

pub fn p_limit() -> [] int {
    return 5;
}

pub fn p_most() -> [] int {
    return 6;
}

pub fn p_from() -> [] int {
    return 7;
}

pub fn p_max_groups() -> [] int {
    return 8;
}

pub fn p_max_distinct() -> [] int {
    return 9;
}

pub fn p_max_state() -> [] int {
    return 10;
}

pub fn p_cap() -> [] int {
    return 11;
}

pub fn p_base_line() -> [] int {
    return 12;
}

pub fn p_yield() -> [] int {
    return 13;
}

pub fn p_size() -> [] int {
    return 15;
}

pub fn p_count() -> [] int {
    return 16;
}

// `lines.fill_file` for a handle read at an offset: the same refill, from the bytes
// at `base + (what has been read)` rather than from a cursor.
pub fn fill_at[&f](r: lines.Lines, file: &!f File, base: int) -> [file_read] lines.Lines {
    let lines.Lines { chunk, pos, held, cap, seen, begin, count, over, ended, newline, ready, read, nul, failed, viewing, view_from, view_to } = r;
    var c = chunk;
    var e = ended;
    var got = 0;
    var bad = failed;
    borrow mut c as &!cw in {
        buffer.clear(cw);
        match file_pread(file, base + read, buffer.room(cw)) {
            Read::Got(n) => {
                buffer.filled(cw, n);
                got = n;
            }
            Read::End => {
                e = true;
            }
            Read::Failed(reason) => {
                e = true;
                bad = reason;
                if bad == 0 {
                    bad = 5;
                }
            }
        }
    }
    return lines.Lines { chunk: c, pos: 0, held: held, cap: cap, seen: seen, begin: begin, count: count, over: over, ended: e, newline: newline, ready: ready, read: read + got, nul: nul, failed: bad, viewing: false, view_from: 0, view_to: 0 };
}

// `lines.next` for the common case, through a unique reference, so that the reader (seventeen fields) is not
// moved in and out of a call for each line: a whole line in the chunk, none held over from the one before,
// not longer than the cap. Answers `lines.line()` with the reader as `lines.next` leaves it, or 9, having
// changed nothing `lines.next` does not change at its start, when the line is anything else (the chunk is
// used up, the line goes on in the next one, it is too long, the input has ended): then the caller asks
// `lines.next`.
pub fn next_fast[&r](r: &!r lines.Lines) -> [] int {
    if r.ready {
        buffer.clear(r.held);
        r.over = 0;
        r.begin = r.seen;
        r.ready = false;
    }
    if r.over > 0 || buffer.size(r.held) > 0 {
        return 9;
    }
    let data = buffer.bytes(r.chunk);
    let at = r.pos;
    let found = index_of_byte(data[at..len(data)], byte_of(10));
    if found < 0 || found > r.cap {
        return 9;
    }
    let k = at + found;
    r.viewing = true;
    r.view_from = at;
    r.view_to = k;
    r.seen = r.seen + (k + 1 - at);
    r.pos = k + 1;
    r.newline = true;
    r.count = r.count + 1;
    r.ready = true;
    return lines.line();
}

// Scan the data records that start in `[start, until)`. `par` carries the parameters
// (`p_*`), `a` the tally (it continues from what it holds), the buffers and the groups
// are the read's. Answers where the next record starts (the offset of the first record
// at or after `until`, the end of the input, or where a block of csv was cut), the
// number of lines before it, a status (0 the range was read, 1 a block of csv is
// pending and the read can be resumed at the offset answered, 2 it stopped because
// `a` says so: an abort, a full page, the row bound), and the buffers and groups.
pub fn scan_range[&h, &f, &p, &q, &c, &s, &w, &a](heap: &!h Heap, file: &!f File, par: &p [int], tree: &q query.Query, cols: &c [int], sel: &s [int], cells: &!w [int], a: &!a [int], rows: buffer.Buffer, scratch: buffer.Buffer, escr: buffer.Buffer, kept: buffer.Buffer, groups: agg.Groups, start: int, until: int) -> [heap, file_read] (int, int, int, buffer.Buffer, buffer.Buffer, buffer.Buffer, buffer.Buffer, agg.Groups) {
    let delim = par[p_delim()];
    let mode = par[p_mode()];
    let as_csv = par[p_csv()] == 1;
    let picked = par[p_picked()];
    let budget = par[p_budget()];
    let limit = par[p_limit()];
    let most = par[p_most()];
    let from = par[p_from()];
    let max_groups = par[p_max_groups()];
    let max_distinct = par[p_max_distinct()];
    let max_state = par[p_max_state()];
    let cap = par[p_cap()];
    let base_line = par[p_base_line()];
    let yield_at = par[p_yield()];
    let filtering = query.count_of(tree, 1) > 0;
    let fast_ok = engine.fast_ok(tree);
    var rows2 = rows;
    var scratch2 = scratch;
    var escr2 = escr;
    var kept2 = kept;
    var groups2 = groups;
    var rec = buffer.empty(heap, 256);
    var quoted = false;
    var seps = 0;
    var opened = 0;
    var r = lines.start(heap, cap);
    var going = true;
    var next_at = par[p_size()];
    var consumed = 0;
    var status = 0;
    while going {
        var state = 9;
        borrow mut r as &!rw in {
            state = next_fast(rw);
        }
        if state == 9 {
            let (stepped, answer) = lines.next(heap, r);
            r = stepped;
            state = answer;
        }
        if state == lines.need() {
            r = fill_at(r, file, start);
        } else if state == lines.done() {
            borrow r as &rr in {
                consumed = lines.number(rr);
                next_at = start + lines.consumed(rr);
            }
            going = false;
        } else if state == lines.long() {
            a[engine.k_abort()] = 4;
            borrow r as &rr in {
                a[engine.k_abort_line()] = base_line + lines.number(rr);
                a[engine.k_long()] = lines.length(rr);
            }
            status = 2;
            going = false;
        } else {
            borrow r as &rr in {
                let number = lines.number(rr);
                var line = lines.text(rr);
                let whole = line;
                if len(line) > 0 && int_of(line[len(line) - 1]) == 13 {
                    line = line[0..len(line) - 1];
                }
                let at = start + lines.offset(rr);
                if !quoted && len(line) == 0 {
                    // A blank line between records is not a record.
                } else if !quoted && at >= until {
                    // The next range's: where this one ends.
                    consumed = number - 1;
                    next_at = at;
                    going = false;
                } else if !quoted && a[engine.k_records()] >= most {
                    a[engine.k_capped()] = 1;
                    status = 2;
                    going = false;
                } else if !quoted && mode == 1 && !filtering && a[engine.k_records()] >= from && a[engine.k_emitted()] >= limit {
                    a[engine.k_more()] = 1;
                    a[engine.k_next()] = a[engine.k_records()];
                    status = 2;
                    going = false;
                } else if !quoted {
                    // A data record begins; most are whole on this line.
                    var inside = false;
                    var found = 0;
                    var bad = false;
                    let (n, open, wrong) = reader.fields(line, delim, cells, a[engine.k_columns()]);
                    found = n;
                    inside = open;
                    bad = wrong;
                    if bad {
                        a[engine.k_abort()] = 1;
                        a[engine.k_abort_line()] = base_line + number;
                        status = 2;
                        going = false;
                    } else if inside {
                        quoted = true;
                        opened = number;
                        borrow mut rec as &!rw in {
                            buffer.clear(rw);
                        }
                        rec = buffer.append(heap, rec, whole);
                        rec = buffer.push(heap, rec, byte_of(10));
                    } else if engine.count_row(a, found, base_line + number, from) {
                        if mode == 1 {
                            let (r3, s3, e3, k3) = engine.process_rows_buf(heap, rows2, scratch2, escr2, kept2, tree, cols, line, cells, sel, picked, delim, as_csv, budget, base_line + number, limit, a);
                            rows2 = r3;
                            scratch2 = s3;
                            escr2 = e3;
                            kept2 = k3;
                        } else {
                            var hot = 1;
                            if !fast_ok {
                                // a distinct count is not added in place: `process_groups`
                            } else if !filtering {
                                var got = 1;
                                borrow mut groups2 as &!gw in {
                                    got = engine.group_plain(gw, tree, cols, line, cells);
                                }
                                if got >= 16 {
                                    kept2 = engine.group_refused(heap, kept2, tree, cols, line, cells, base_line + number, got, a);
                                    hot = 0;
                                } else {
                                    hot = got;
                                }
                            } else {
                                borrow mut groups2 as &!gw in {
                                    let (f2, e2, k2) = engine.group_fast(heap, gw, escr2, kept2, tree, cols, line, cells, base_line + number, a);
                                    hot = f2;
                                    escr2 = e2;
                                    kept2 = k2;
                                }
                            }
                            if hot == 1 || hot == 3 {
                                let (g3, e3, k3) = engine.process_groups(heap, groups2, escr2, kept2, tree, cols, line, cells, base_line + number, max_groups, max_distinct, max_state, hot == 3, a);
                                groups2 = g3;
                                escr2 = e3;
                                kept2 = k3;
                            }
                        }
                    }
                } else {
                    // The rest of a record that has quotes open: kept.
                    let (inside, n, bad) = reader.scan(line, delim, quoted, seps);
                    seps = n;
                    rec = buffer.append(heap, rec, whole);
                    var held = 0;
                    borrow rec as &rw in {
                        held = buffer.size(rw);
                    }
                    if bad {
                        a[engine.k_abort()] = 1;
                        a[engine.k_abort_line()] = base_line + number;
                        status = 2;
                        going = false;
                    } else if inside {
                        rec = buffer.push(heap, rec, byte_of(10));
                        if held + 1 > cap {
                            a[engine.k_abort()] = 7;
                            a[engine.k_abort_line()] = base_line + opened;
                            status = 2;
                            going = false;
                        }
                    } else {
                        quoted = false;
                        seps = 0;
                        // A record that spanned lines, whole in `rec`.
                        borrow rec as &rw in {
                            let whole_len = buffer.size(rw);
                            var record = buffer.bytes(rw);
                            if whole_len > 0 && int_of(record[whole_len - 1]) == 13 {
                                record = record[0..whole_len - 1];
                            }
                            var found = 0;
                            var wrong = false;
                            let (m, open, bad_quote) = reader.fields(record, delim, cells, a[engine.k_columns()]);
                            found = m;
                            wrong = bad_quote || open;
                            if wrong {
                                a[engine.k_abort()] = 1;
                                a[engine.k_abort_line()] = base_line + opened;
                                status = 2;
                                going = false;
                            } else if engine.count_row(a, found, base_line + opened, from) {
                                if mode == 1 {
                                    let (r3, s3, e3, k3) = engine.process_rows_buf(heap, rows2, scratch2, escr2, kept2, tree, cols, record, cells, sel, picked, delim, as_csv, budget, base_line + opened, limit, a);
                                    rows2 = r3;
                                    scratch2 = s3;
                                    escr2 = e3;
                                    kept2 = k3;
                                } else {
                                    var hot = 1;
                                    if !fast_ok {
                                        // a distinct count is not added in place: `process_groups`
                                    } else if !filtering {
                                        var got = 1;
                                        borrow mut groups2 as &!gw in {
                                            got = engine.group_plain(gw, tree, cols, record, cells);
                                        }
                                        if got >= 16 {
                                            kept2 = engine.group_refused(heap, kept2, tree, cols, record, cells, base_line + opened, got, a);
                                            hot = 0;
                                        } else {
                                            hot = got;
                                        }
                                    } else {
                                        borrow mut groups2 as &!gw in {
                                            let (f2, e2, k2) = engine.group_fast(heap, gw, escr2, kept2, tree, cols, record, cells, base_line + opened, a);
                                            hot = f2;
                                            escr2 = e2;
                                            kept2 = k2;
                                        }
                                    }
                                    if hot == 1 || hot == 3 {
                                        let (g3, e3, k3) = engine.process_groups(heap, groups2, escr2, kept2, tree, cols, record, cells, base_line + opened, max_groups, max_distinct, max_state, hot == 3, a);
                                        groups2 = g3;
                                        escr2 = e3;
                                        kept2 = k3;
                                    }
                                }
                            }
                        }
                    }
                }
                if a[engine.k_stop()] == 1 && going {
                    status = 2;
                    going = false;
                }
                // A block of csv is pending: the caller writes it, and resumes after this line.
                if going && !quoted && yield_at > 0 {
                    var held_out = 0;
                    borrow rows2 as &pr in {
                        held_out = buffer.size(pr);
                    }
                    if held_out >= yield_at {
                        status = 1;
                        consumed = number;
                        next_at = start + lines.consumed(rr);
                        going = false;
                    }
                }
            }
        }
    }
    // The input ended with a quote open: not a record.
    if quoted && a[engine.k_abort()] == 0 {
        a[engine.k_abort()] = 2;
        a[engine.k_abort_line()] = base_line + opened;
        status = 2;
    }
    buffer.drop(heap, rec);
    lines.drop(heap, r);
    return (next_at, consumed, status, rows2, scratch2, escr2, kept2, groups2);
}
