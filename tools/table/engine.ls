edition 5;

module engine;

// The per-row machinery both ways of reading share: the tally of a read (an array
// of ints, `k_*` say where), the accounting of a record (counted, ragged, before
// `from`), the test of `--where`, and what is done with a row that passes it: written
// (`emit_buf`, which never writes to standard output; `emit_row`, which does, when
// a block of csv is pending) or added to its group. The sequential read in table.ls
// and the scan of a byte range in scan.ls (what a worker thread runs, and what the
// parent runs again when a worker's answer cannot be trusted) call the same ones.

import std.buffer;
import std.bytes;
import std.vec;
import agg;
import expr;
import query;
import sorter;
import toolbox.out;
import writer;

pub fn k_records() -> [] int {
    return 0;
}

pub fn k_ragged() -> [] int {
    return 1;
}

pub fn k_first_row() -> [] int {
    return 2;
}

pub fn k_first_line() -> [] int {
    return 3;
}

pub fn k_first_found() -> [] int {
    return 4;
}

pub fn k_capped() -> [] int {
    return 5;
}

pub fn k_abort() -> [] int {
    return 6;
}

pub fn k_abort_line() -> [] int {
    return 7;
}

pub fn k_long() -> [] int {
    return 8;
}

pub fn k_emitted() -> [] int {
    return 9;
}

pub fn k_more() -> [] int {
    return 10;
}

pub fn k_next() -> [] int {
    return 11;
}

pub fn k_columns() -> [] int {
    return 12;
}

pub fn k_bad_name() -> [] int {
    return 13;
}

pub fn k_stop() -> [] int {
    return 14;
}

pub fn k_err_col() -> [] int {
    return 15;
}

pub fn k_err_fn() -> [] int {
    return 16;
}

pub fn k_err_row() -> [] int {
    return 17;
}

pub fn k_err_line() -> [] int {
    return 18;
}

pub fn k_groups() -> [] int {
    return 19;
}

pub fn k_sort_field() -> [] int {
    return 20;
}

pub fn k_sort_slot() -> [] int {
    return 21;
}

pub fn k_sort_desc() -> [] int {
    return 22;
}

pub fn k_size() -> [] int {
    return 32;
}

// One more data record, number `opened`'s line it began on, of `found` fields:
// counted, and answers whether it is a row to hand on (not ragged, and not
// before `from`).
pub fn count_row[&a](a: &!a [int], found: int, opened: int, from: int) -> [] bool {
    let index = a[k_records()];
    a[k_records()] = index + 1;
    if found != a[k_columns()] {
        if a[k_ragged()] == 0 {
            a[k_first_row()] = index + 1;
            a[k_first_line()] = opened;
            a[k_first_found()] = found;
        }
        a[k_ragged()] = a[k_ragged()] + 1;
        return false;
    }
    return index >= from;
}

// Write what is pending, and keep the buffer; false when standard output took
// less.
pub fn flush[&i](io: &!i Io, pending: buffer.Buffer) -> [io_write] (buffer.Buffer, bool) {
    var ok = true;
    var held = pending;
    borrow held as &r in {
        ok = out.emit(io, buffer.bytes(r));
    }
    borrow mut held as &!w in {
        buffer.clear(w);
    }
    return (held, ok);
}

// One row of the selection, from the record `record` whose fields are in
// `cells`, as csv or json appended to `rows` (writing nothing out: see `emit_row`), through `scratch` so that a row that does not fit the
// budget is not half in. Updates the tally: `emitted`, and `stop` with
// `more` and `next` when the page ends here or `abort` when it cannot.
pub fn emit_buf[&h, &d, &c, &s, &a](heap: &!h Heap, rows: buffer.Buffer, scratch: buffer.Buffer, record: &d [byte], cells: &c [int], sel: &s [int], picked: int, delim: int, as_csv: bool, budget: int, a: &!a [int]) -> [heap] (buffer.Buffer, buffer.Buffer) {
    var pending = rows;
    var row = scratch;
    if as_csv {
        var begun = 0;
        borrow pending as &br in {
            begun = buffer.size(br);
        }
        var j = 0;
        while j < picked {
            let at = 3 * sel[j];
            if j > 0 {
                pending = buffer.push(heap, pending, byte_of(delim));
            }
            pending = writer.csv_cell(heap, pending, record, cells[at], cells[at + 1], cells[at + 2], delim);
            j = j + 1;
        }
        var held = 0;
        borrow pending as &r in {
            held = buffer.size(r);
        }
        if picked == 1 && held == begun {
            // A row of one empty field is `""`: a blank line is no record.
            pending = buffer.append(heap, pending, "\"\"");
            held = held + 2;
        }
        pending = buffer.push(heap, pending, byte_of(10));
        a[k_emitted()] = a[k_emitted()] + 1;
        return (pending, row);
    }
    borrow mut row as &!w in {
        buffer.clear(w);
    }
    row = buffer.push(heap, row, byte_of('['));
    var j = 0;
    while j < picked {
        let at = 3 * sel[j];
        if j > 0 {
            row = buffer.push(heap, row, byte_of(','));
        }
        row = writer.json_cell(heap, row, record, cells[at], cells[at + 1], cells[at + 2]);
        j = j + 1;
    }
    row = buffer.push(heap, row, byte_of(']'));
    var have = 0;
    var need = 0;
    borrow pending as &pr in {
        have = buffer.size(pr);
    }
    borrow row as &rr in {
        need = buffer.size(rr);
    }
    if have + need + 1 > budget {
        a[k_stop()] = 1;
        if a[k_emitted()] == 0 {
            a[k_abort()] = 8;
        } else {
            a[k_more()] = 1;
            a[k_next()] = a[k_records()] - 1;
        }
        return (pending, row);
    }
    if a[k_emitted()] > 0 {
        pending = buffer.push(heap, pending, byte_of(','));
    }
    borrow row as &rr in {
        pending = buffer.append(heap, pending, buffer.bytes(rr));
    }
    a[k_emitted()] = a[k_emitted()] + 1;
    return (pending, row);
}

// The same, and as csv a block of it that has become 64 KiB is written to
// standard output and the buffer emptied.
pub fn emit_row[&h, &i, &d, &c, &s, &a](heap: &!h Heap, io: &!i Io, rows: buffer.Buffer, scratch: buffer.Buffer, record: &d [byte], cells: &c [int], sel: &s [int], picked: int, delim: int, as_csv: bool, budget: int, a: &!a [int]) -> [heap, io_write] (buffer.Buffer, buffer.Buffer) {
    let (pending, row) = emit_buf(heap, rows, scratch, record, cells, sel, picked, delim, as_csv, budget, a);
    if !as_csv {
        return (pending, row);
    }
    var held = 0;
    borrow pending as &pr in {
        held = buffer.size(pr);
    }
    if held < 65536 {
        return (pending, row);
    }
    let (after, ok) = flush(io, pending);
    if !ok {
        a[k_abort()] = 9;
        a[k_stop()] = 1;
    }
    return (after, row);
}

// The first 64 bytes of a cell, kept to say which one was refused.
pub fn keep_value[&h, &d](heap: &!h Heap, kept: buffer.Buffer, record: &d [byte], first: int, last: int) -> [heap] buffer.Buffer {
    var v = kept;
    borrow mut v as &!w in {
        buffer.clear(w);
    }
    var stop = last;
    if last - first > 64 {
        stop = first + 64;
    }
    return buffer.append(heap, v, record[first..stop]);
}

// Whether `--where` wants the record: 1 yes (also when there is no condition),
// 0 no, -1 a condition met a cell it cannot compare, which is recorded in `a`
// (the read stops). `escr` is a scratch for a quoted cell, `kept` the first
// bytes of the cell that was refused.
pub fn screen[&h, &q, &c, &d, &e, &a](heap: &!h Heap, tree: &q query.Query, cols: &c [int], record: &d [byte], cells: &e [int], escr: buffer.Buffer, kept: buffer.Buffer, opened: int, a: &!a [int]) -> [heap] (int, buffer.Buffer, buffer.Buffer) {
    if query.count_of(tree, 1) == 0 {
        return (1, escr, kept);
    }
    let (verdict, which, e2) = expr.eval(heap, tree, cols, record, cells, escr);
    if verdict < 2 {
        return (verdict, e2, kept);
    }
    // A condition met a cell it cannot compare.
    let column = cols[query.cond_at(tree, which, 3)];
    a[k_abort()] = 9 + verdict;
    a[k_err_col()] = column;
    a[k_err_fn()] = -1;
    a[k_err_row()] = a[k_records()];
    a[k_err_line()] = opened;
    a[k_stop()] = 1;
    return (-1, e2, keep_value(heap, kept, record, cells[3 * column], cells[3 * column + 1]));
}

// A counted row that is not ragged and is at or past `from`, for rows (mode 1):
// tested by `--where`, then emitted into `rows` (nothing is written out).
pub fn process_rows_buf[&h, &q, &c, &d, &e, &s, &a](heap: &!h Heap, rows: buffer.Buffer, scratch: buffer.Buffer, escr: buffer.Buffer, kept: buffer.Buffer, tree: &q query.Query, cols: &c [int], record: &d [byte], cells: &e [int], sel: &s [int], picked: int, delim: int, as_csv: bool, budget: int, opened: int, limit: int, a: &!a [int]) -> [heap] (buffer.Buffer, buffer.Buffer, buffer.Buffer, buffer.Buffer) {
    let (verdict, e2, k2) = screen(heap, tree, cols, record, cells, escr, kept, opened, a);
    if verdict != 1 {
        return (rows, scratch, e2, k2);
    }
    if a[k_emitted()] >= limit {
        // The page is full and this row matches: there is more.
        a[k_more()] = 1;
        a[k_next()] = a[k_records()] - 1;
        a[k_stop()] = 1;
        return (rows, scratch, e2, k2);
    }
    let (r3, s3) = emit_buf(heap, rows, scratch, record, cells, sel, picked, delim, as_csv, budget, a);
    return (r3, s3, e2, k2);
}

// The same, writing out a block of csv that has become 64 KiB.
pub fn process_rows[&h, &i, &q, &c, &d, &e, &s, &a](heap: &!h Heap, io: &!i Io, rows: buffer.Buffer, scratch: buffer.Buffer, escr: buffer.Buffer, kept: buffer.Buffer, tree: &q query.Query, cols: &c [int], record: &d [byte], cells: &e [int], sel: &s [int], picked: int, delim: int, as_csv: bool, budget: int, opened: int, limit: int, a: &!a [int]) -> [heap, io_write] (buffer.Buffer, buffer.Buffer, buffer.Buffer, buffer.Buffer) {
    let (r3, s3, e2, k2) = process_rows_buf(heap, rows, scratch, escr, kept, tree, cols, record, cells, sel, picked, delim, as_csv, budget, opened, limit, a);
    if !as_csv || a[k_stop()] == 1 {
        return (r3, s3, e2, k2);
    }
    var held = 0;
    borrow r3 as &pr in {
        held = buffer.size(pr);
    }
    if held < 65536 {
        return (r3, s3, e2, k2);
    }
    let (after, ok) = flush(io, r3);
    if !ok {
        a[k_abort()] = 9;
        a[k_stop()] = 1;
    }
    return (after, s3, e2, k2);
}

// The same for a grouping (mode 2): tested, then added to its group.
pub fn process_groups[&h, &q, &c, &d, &e, &a](heap: &!h Heap, groups: agg.Groups, escr: buffer.Buffer, kept: buffer.Buffer, tree: &q query.Query, cols: &c [int], record: &d [byte], cells: &e [int], opened: int, max_groups: int, max_distinct: int, max_state: int, track: bool, keyed: bool, a: &!a [int]) -> [heap] (agg.Groups, buffer.Buffer, buffer.Buffer) {
    if query.count_of(tree, 4) > 0 {
        // `--order-by`: the rows are held, not grouped; the bounds are the sort's (see sorter.ls)
        return order_row(heap, groups, escr, kept, tree, cols, record, cells, opened, max_groups, max_state, a);
    }
    let (verdict, e2, k2) = screen(heap, tree, cols, record, cells, escr, kept, opened, a);
    if verdict != 1 {
        return (groups, e2, k2);
    }
    let (g3, e3, status, k) = agg.add(heap, groups, tree, cols, record, cells, e2, max_groups, max_distinct, max_state, track, keyed);
    if status == 0 {
        return (g3, e3, k2);
    }
    a[k_stop()] = 1;
    a[k_abort()] = 12 + status;
    if status < 4 {
        return (g3, e3, k2);
    }
    let column = cols[query.agg_at(tree, k, 1)];
    a[k_err_col()] = column;
    a[k_err_fn()] = query.agg_at(tree, k, 0);
    a[k_err_row()] = a[k_records()];
    a[k_err_line()] = opened;
    return (g3, e3, keep_value(heap, k2, record, cells[3 * column], cells[3 * column + 1]));
}

// What `process_groups` does for a row whose group exists and whose cells are plain (see `agg.add_fast`),
// through a unique reference to the groups, so that they are not moved in and out for each row. Answers
// 0 when the row is dealt with (not wanted, added, or refused with `a` set), 1 when `process_groups` has to
// deal with it, 3 when it has to and the key is built in the groups' key buffer (a new group: `keyed`), and
// the buffers.
pub fn group_fast[&h, &g, &q, &c, &d, &e, &a](heap: &!h Heap, groups: &!g agg.Groups, escr: buffer.Buffer, kept: buffer.Buffer, tree: &q query.Query, cols: &c [int], record: &d [byte], cells: &e [int], opened: int, track: bool, a: &!a [int]) -> [heap] (int, buffer.Buffer, buffer.Buffer) {
    let (verdict, e2, k2) = screen(heap, tree, cols, record, cells, escr, kept, opened, a);
    if verdict != 1 {
        return (0, e2, k2);
    }
    let (status, k) = agg.add_fast(groups, tree, cols, record, cells, track);
    if status == 0 {
        return (0, e2, k2);
    }
    if status < 0 {
        return (1 - 2 * status - 2, e2, k2);
    }
    // The same refusal as `process_groups`.
    a[k_stop()] = 1;
    a[k_abort()] = 12 + status;
    let column = cols[query.agg_at(tree, k, 1)];
    a[k_err_col()] = column;
    a[k_err_fn()] = query.agg_at(tree, k, 0);
    a[k_err_row()] = a[k_records()];
    a[k_err_line()] = opened;
    return (0, e2, keep_value(heap, k2, record, cells[3 * column], cells[3 * column + 1]));
}

// `group_fast` for a grouping with no `--where`: nothing to screen, so no buffers go in or out. Answers 0
// when the row is added, 1 when `process_groups` has to deal with it (3 when the key is built: a new group), and 16 + 8 * k + status when aggregate
// `k` refused the row with `status` (4 to 6), for `group_refused` to record.
pub fn group_plain[&g, &q, &c, &d, &e](groups: &!g agg.Groups, tree: &q query.Query, cols: &c [int], record: &d [byte], cells: &e [int], track: bool) -> [] int {
    let (status, k) = agg.add_fast(groups, tree, cols, record, cells, track);
    if status == 0 {
        return 0;
    }
    if status < 0 {
        return 1 - 2 * status - 2;
    }
    return 16 + 8 * k + status;
}

// The refusal `group_plain` answered, recorded as `process_groups` records it.
pub fn group_refused[&h, &q, &c, &d, &e, &a](heap: &!h Heap, kept: buffer.Buffer, tree: &q query.Query, cols: &c [int], record: &d [byte], cells: &e [int], opened: int, answer: int, a: &!a [int]) -> [heap] buffer.Buffer {
    let status = (answer - 16) % 8;
    let k = (answer - 16) / 8;
    a[k_stop()] = 1;
    a[k_abort()] = 12 + status;
    let column = cols[query.agg_at(tree, k, 1)];
    a[k_err_col()] = column;
    a[k_err_fn()] = query.agg_at(tree, k, 0);
    a[k_err_row()] = a[k_records()];
    a[k_err_line()] = opened;
    return keep_value(heap, kept, record, cells[3 * column], cells[3 * column + 1]);
}

// Whether `add_fast` can ever take a row of this plan: not when an aggregate is `distinct`, which keeps a set of
// pairs and is added the old way (so the rows of such a plan do not go through the fast call at all).
pub fn fast_ok[&q](tree: &q query.Query) -> [] bool {
    if query.count_of(tree, 4) > 0 {
        return false;
    }
    var k = 0;
    while k < query.agg_count(tree) {
        if query.agg_at(tree, k, 0) == 4 {
            return false;
        }
        k = k + 1;
    }
    return true;
}

// A counted row that is not ragged, for `--order-by`: tested by `--where`, then held by the sorter (the groups, see
// sorter.ls), or, when only the first rows are wanted and the last of them is known, dropped at once if it is not before
// that one. `max_rows` and `max_state` are the bounds. Sets `abort` for a bound passed (20 rows, 21 bytes) or a key that is
// not an integer (as `--where` does, with `err_fn` -2); once `2 * cap + 1` rows are held they are cut back to `cap`.
fn order_row[&h, &q, &c, &d, &e, &a](heap: &!h Heap, held: agg.Groups, escr: buffer.Buffer, kept: buffer.Buffer, tree: &q query.Query, cols: &c [int], record: &d [byte], cells: &e [int], opened: int, max_rows: int, max_state: int, a: &!a [int]) -> [heap] (agg.Groups, buffer.Buffer, buffer.Buffer) {
    let (verdict, e2, k2) = screen(heap, tree, cols, record, cells, escr, kept, opened, a);
    if verdict != 1 {
        return (held, e2, k2);
    }
    var rejected = 0;
    borrow held as &hr in {
        rejected = sorter.reject(hr, record, cells);
    }
    if rejected == 1 {
        return (held, e2, k2);
    }
    let (s2, e3, status, which) = sorter.add(heap, held, record, cells, e2, max_rows, max_state);
    if status == 0 {
        var out = s2;
        var full = false;
        borrow out as &or in {
            full = contents(or.memo)[0] > 0 && sorter.held(or) >= 2 * contents(or.memo)[0] + 1;
        }
        if full {
            out = sorter.cut_back(heap, out);
        }
        return (out, e3, k2);
    }
    a[k_stop()] = 1;
    if status == 1 {
        a[k_abort()] = 20;
        return (s2, e3, k2);
    }
    if status == 2 {
        a[k_abort()] = 21;
        return (s2, e3, k2);
    }
    a[k_abort()] = 8 + status;
    var column = 0;
    borrow s2 as &sr in {
        column = contents(sr.memo)[4 + 3 * which];
    }
    a[k_err_col()] = column;
    a[k_err_fn()] = -2;
    a[k_err_row()] = a[k_records()];
    a[k_err_line()] = opened;
    return (s2, e3, keep_value(heap, k2, record, cells[3 * column], cells[3 * column + 1]));
}
