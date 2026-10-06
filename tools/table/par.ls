edition 5;

module par;

// The data records of the file read by several threads, and answered as the sequential
// read answers: the same bytes, the same refusal, the same row and line named. docs/parallel.md
// is the design and what it rests on; this is the shape of it.
//
// The file after the header is cut into ranges of about `chunk` bytes, each beginning
// where a line does (`align`). A wave of up to `threads` ranges is read at once, each by
// a thread of its own with a `File` of its own and an `Heap` forked from the parent's, and
// each *assuming* its first line begins a record, which is true unless a quoted field
// holds the newline before it. A range's records are the ones that start in it, and the
// last may run into the next range, so a range ends where its last record ends, and the
// next range's guess is right exactly when that is where the next range begins. The
// parent takes the ranges in file order, keeping a position `cur` (where the sequential
// read would be): a range whose guess is right, whose thread finished without anything
// that the sequential read would have stopped at, and whose effect on the answer can be
// added without reading the file in order (`accept`) is taken as it is; anything else the
// parent reads again itself, from `cur`, with the sequential rules and its own state. So
// the answer is the sequential one by construction, wherever the threads were
// speculating wrongly or the answer depends on order, and faster only where they were
// not.
//
// What a thread can hand back is bytes in a slot the parent owns (a `join` result is one
// leaf, and a field of a `&!` struct cannot be replaced), and each thread's frame is one
// level of a recursion (a spawn needs a function value of its own and a reference
// that lives until its join, so the number of threads is the depth of a call, not a loop).

import std.buffer;
import std.vec;
import agg;
import engine;
import query;
import scan;
import toolbox.out;
import toolbox.path;
import toolbox.place;

// The slot: a header of 36 integers, then the payload.
//   0 status (0 the range was read and nothing in it needs the parent, 1 it does, 2 not run)
//   1 where the next record starts   2 lines before it   3 the payload's length
//   4.. the tally of the range (`engine.k_*`, 32 integers)
pub fn header_bytes() -> [] int {
    return 8 * 36;
}

fn j_start() -> [] int {
    return scan.p_count();
}

fn j_until() -> [] int {
    return scan.p_count() + 1;
}

fn j_columns() -> [] int {
    return scan.p_count() + 2;
}

fn j_payload() -> [] int {
    return scan.p_count() + 3;
}

fn j_size() -> [] int {
    return 24;
}

res struct Job {
    heap: Heap,
    file: File,
    tree: query.Query,
    cols: Box[[int]],
    sel: Box[[int]],
    par: Box[[int]],
    out: Box[[byte]],
}

// A thread: read the range of its job, write the answer into its slot.
fn work[&r](w: &!r Job) -> [heap, file_read] int {
    let na = query.agg_count(w.tree);
    let mode = contents(w.par)[scan.p_mode()];
    var a = box_slice(w.heap, engine.k_size(), 0);
    var cells = box_slice(w.heap, 3 * (contents(w.par)[j_columns()] + 1), 0);
    var rows = buffer.empty(w.heap, 65536);
    var scratch = buffer.empty(w.heap, 256);
    var escr = buffer.empty(w.heap, 16);
    var kept = buffer.empty(w.heap, 64);
    var groups = agg.start(w.heap, na);
    var next_at = 0;
    var lines_read = 0;
    var status = 0;
    var clean = 0;
    var used = 0;
    var payload = 0;
    borrow mut a as &!aw in {
        borrow mut cells as &!cw in {
            let t = contents(aw);
            t[engine.k_columns()] = contents(w.par)[j_columns()];
            let (x, l, s, r2, s2, e2, k2, g2) = scan.scan_range(w.heap, w.file, contents(w.par), w.tree, contents(w.cols), contents(w.sel), contents(cw), t, rows, scratch, escr, kept, groups, contents(w.par)[j_start()], contents(w.par)[j_until()]);
            next_at = x;
            lines_read = l;
            status = s;
            rows = r2;
            scratch = s2;
            escr = e2;
            kept = k2;
            groups = g2;
            if s == 0 && t[engine.k_abort()] == 0 && t[engine.k_stop()] == 0 && t[engine.k_capped()] == 0 && t[engine.k_more()] == 0 {
                clean = 1;
            }
            // The answer: the payload first (so that its size can be told), then the header.
            let o = contents(w.out);
            let room = len(o) - header_bytes();
            if clean == 1 && mode == 1 {
                borrow rows as &rr in {
                    let bytes_out = buffer.bytes(rr);
                    if len(bytes_out) <= room {
                        copy_into(o[header_bytes()..header_bytes() + len(bytes_out)], bytes_out);
                        payload = len(bytes_out);
                    } else {
                        clean = 0;
                    }
                }
            } else if clean == 1 {
                borrow groups as &gr in {
                    let n = agg.serialize(gr, o[header_bytes()..len(o)]);
                    if n < 0 {
                        clean = 0;
                    } else {
                        payload = n;
                    }
                }
            }
            if clean == 1 {
                agg.put_i64(o, 0, 0);
            } else {
                agg.put_i64(o, 0, 1);
            }
            agg.put_i64(o, 8, next_at);
            agg.put_i64(o, 16, lines_read);
            agg.put_i64(o, 24, payload);
            var i = 0;
            while i < engine.k_size() {
                agg.put_i64(o, 32 + 8 * i, t[i]);
                i = i + 1;
            }
        }
    }
    unbox_slice(w.heap, a);
    unbox_slice(w.heap, cells);
    buffer.drop(w.heap, rows);
    buffer.drop(w.heap, scratch);
    buffer.drop(w.heap, escr);
    buffer.drop(w.heap, kept);
    agg.drop(w.heap, groups);
    return 0;
}

fn copy_ints[&h, &s](heap: &!h Heap, from: &s [int]) -> [heap] Box[[int]] {
    let out = box_slice(heap, len(from) + 1, 0);
    var o = out;
    borrow mut o as &!ow in {
        let t = contents(ow);
        var i = 0;
        while i < len(from) {
            t[i] = from[i];
            i = i + 1;
        }
    }
    return o;
}

// One level of a wave: make job `k`, start its thread, make the rest, join it, and move its
// slot up. `plan` holds where the ranges begin (`plan[k]`, and `plan[n]` where the last ends).
fn fan[&p, &f, &x, &y, &z, &t, &c, &s, &r, &l, &u](parent: &!p Heap, fs: &f Fs(""), root: &x [byte], rel: &y [byte], full: &z [byte], tree: &t query.Query, cols: &c [int], sel: &s [int], par: &r [int], plan: &l [int], slots: &!u [byte], k: int, n: int, slot_bytes: int) -> [heap, conc, file_read, fs_read(""), dir_read] int {
    if k >= n {
        return 0;
    }
    match place.open_operand(fs, root, rel, full) {
        Opened::Failed(reason) => {
            return 1;
        }
        Opened::Ok(file) => {
            let forked = fork_heap(parent);
            let own = copy_ints(parent, par);
            var job = Job { heap: forked, file: file, tree: query.duplicate(parent, tree), cols: copy_ints(parent, cols), sel: copy_ints(parent, sel), par: own, out: box_slice(parent, slot_bytes, byte_of(0)) };
            borrow mut job as &!kj in {
                contents(kj.par)[j_start()] = plan[k];
                contents(kj.par)[j_until()] = plan[k + 1];
            }
            var rest = 0;
            borrow mut job as &!kj in {
                let body = work;
                let thread = spawn(kj, body);
                rest = fan(parent, fs, root, rel, full, tree, cols, sel, par, plan, slots, k + 1, n, slot_bytes);
                join(thread);
            }
            let Job { heap, file, tree, cols, sel, par, out } = job;
            file_close(file);
            release(heap);
            query.drop(parent, tree);
            unbox_slice(parent, cols);
            unbox_slice(parent, sel);
            unbox_slice(parent, par);
            var used = header_bytes();
            borrow out as &ob in {
                used = used + agg.get_i64(contents(ob), 24);
                copy_into(slots[k * slot_bytes..k * slot_bytes + used], contents(ob)[0..used]);
            }
            unbox_slice(parent, out);
            return rest;
        }
    }
}

// The first offset at or after `p` that begins a line (the byte before it is a newline), or
// `size` when there is none within a megabyte: a range that cannot be aligned is not made.
fn align[&h, &f](heap: &!h Heap, file: &!f File, p: int, size: int) -> [heap, file_read] int {
    if p >= size {
        return size;
    }
    var window = buffer.empty(heap, 65536);
    var at = p - 1;
    var found = size;
    var rounds = 0;
    var going = true;
    while going && rounds < 16 {
        var got = 0;
        borrow mut window as &!ww in {
            buffer.clear(ww);
            match file_pread(file, at, buffer.room(ww)) {
                Read::Got(n) => {
                    buffer.filled(ww, n);
                    got = n;
                }
                Read::End => {
                    got = 0;
                }
                Read::Failed(reason) => {
                    got = 0;
                }
            }
        }
        if got == 0 {
            going = false;
        } else {
            var q = -1;
            borrow window as &wr in {
                q = index_of_byte(buffer.bytes(wr), byte_of(10));
            }
            if q >= 0 {
                found = at + q + 1;
                going = false;
            } else {
                at = at + got;
                rounds = rounds + 1;
            }
        }
    }
    buffer.drop(heap, window);
    return found;
}

// Read everything from `start` (where a data record begins, after `lines0` lines) to the
// end of the file, `size`, with up to `threads` threads, in ranges of about `chunk`
// bytes. `pp` are the sequential read's parameters (`scan.p_*`), `a` its tally, the
// buffers and the groups its output so far; answered are the same, as the sequential read
// would have left them. `fs`, `root`, `rel` and `full` are what a thread needs to open the
// file again.
pub fn run[&h, &i, &f, &g, &x, &y, &z, &q, &c, &s, &w, &t, &v](heap: &!h Heap, io: &!i Io, file: &!f File, fs: &g Fs(""), root: &x [byte], rel: &y [byte], full: &z [byte], tree: &q query.Query, cols: &c [int], sel: &s [int], cells: &!w [int], a: &!t [int], pp: &!v [int], rows: buffer.Buffer, scratch: buffer.Buffer, escr: buffer.Buffer, kept: buffer.Buffer, groups: agg.Groups, start: int, lines0: int, size: int, threads: int, most_chunk: int) -> [heap, io_write, conc, file_read, fs_read(""), dir_read] (buffer.Buffer, buffer.Buffer, buffer.Buffer, buffer.Buffer, agg.Groups) {
    // A range is at most `most_chunk`, and no more than a thread's share of what is left: every
    // thread has something to read, and the memory is what the ranges are, not what the limit is.
    var chunk = most_chunk;
    let share = (size - start + threads - 1) / threads;
    if share < chunk {
        chunk = share;
    }
    if chunk < 1 {
        chunk = 1;
    }
    let mode = pp[scan.p_mode()];
    let as_csv = pp[scan.p_csv()] == 1;
    let most = pp[scan.p_most()];
    let limit = pp[scan.p_limit()];
    let budget = pp[scan.p_budget()];
    let filtering = query.count_of(tree, 1) > 0;
    let payload_cap = chunk + 65536;
    let slot_bytes = header_bytes() + payload_cap;
    let wide = 1000000000000000;
    // What every thread is told: the parameters, except that it counts its range alone.
    var wpar = box_slice(heap, j_size(), 0);
    borrow mut wpar as &!wp in {
        let u = contents(wp);
        var k = 0;
        while k < scan.p_count() {
            u[k] = pp[k];
            k = k + 1;
        }
        u[scan.p_budget()] = wide;
        u[scan.p_limit()] = wide;
        u[scan.p_most()] = wide;
        u[scan.p_from()] = 0;
        u[scan.p_base_line()] = 0;
        u[scan.p_yield()] = payload_cap;
        u[scan.p_track()] = 0;
        if mode == 2 {
            u[scan.p_track()] = 1;
        }
        u[j_columns()] = a[engine.k_columns()];
    }
    var slots = box_slice(heap, threads * slot_bytes + 1, byte_of(0));
    var plan = box_slice(heap, threads + 2, 0);
    var rows2 = rows;
    var scratch2 = scratch;
    var escr2 = escr;
    var kept2 = kept;
    var groups2 = groups;
    var cur = start;
    var lines_before = lines0;
    var going = true;
    // A page that is not the whole answer may end in the first range: the waves start with one
    // range and double, so that what is read beyond the page is never more than what was needed.
    var allowed = threads;
    if mode == 1 && limit < wide {
        allowed = 1;
    }
    while going && cur < size && a[engine.k_stop()] == 0 && a[engine.k_abort()] == 0 {
        let wave_began = cur;
        var n = allowed;
        if allowed < threads {
            allowed = allowed * 2;
            if allowed > threads {
                allowed = threads;
            }
        }
        let ranges = (size - cur + chunk - 1) / chunk;
        if ranges < n {
            n = ranges;
        }
        // Where the ranges begin: `cur`, then the first line start at or after each chunk's worth.
        borrow mut plan as &!pw in {
            let pl = contents(pw);
            pl[0] = cur;
            var j = 1;
            while j <= n {
                var edge = align(heap, file, cur + j * chunk, size);
                if edge < pl[j - 1] {
                    edge = pl[j - 1];
                }
                pl[j] = edge;
                j = j + 1;
            }
            if cur + n * chunk >= size {
                pl[n] = size;
            }
        }
        borrow mut slots as &!sw in {
            var j = 0;
            while j < n {
                agg.put_i64(contents(sw), j * slot_bytes, 2);
                j = j + 1;
            }
            borrow wpar as &wr in {
                borrow plan as &pr in {
                    fan(heap, fs, root, rel, full, tree, cols, sel, contents(wr), contents(pr), contents(sw), 0, n, slot_bytes);
                }
            }
        }
        // The ranges, in file order.
        var j = 0;
        while j < n && going {
            var until = 0;
            borrow plan as &pr in {
                until = contents(pr)[j + 1];
            }
            if cur < until {
                var accepted = false;
                borrow slots as &sr in {
                    let sl = contents(sr);
                    let at = j * slot_bytes;
                    var first = 0;
                    borrow plan as &pr in {
                        first = contents(pr)[j];
                    }
                    if agg.get_i64(sl, at) == 0 && first == cur {
                        let r = agg.get_i64(sl, at + 32 + 8 * engine.k_records());
                        let g = agg.get_i64(sl, at + 32 + 8 * engine.k_ragged());
                        let e = agg.get_i64(sl, at + 32 + 8 * engine.k_emitted());
                        let payload_len = agg.get_i64(sl, at + 24);
                        let before = a[engine.k_records()];
                        var fits = before < most && before + r <= most;
                        if mode == 1 && fits {
                            // Without a condition the page stops at the first record after its last row,
                            // ragged or not: a range that reaches the limit has to be read in order.
                            if !filtering && a[engine.k_emitted()] + e >= limit && r > 0 {
                                fits = false;
                            }
                            if a[engine.k_emitted()] + e > limit {
                                fits = false;
                            }
                            if !as_csv {
                                // Every row of the range would have passed the budget's test.
                                var have = 0;
                                borrow rows2 as &rr in {
                                    have = buffer.size(rr);
                                }
                                if have + payload_len + 2 > budget {
                                    fits = false;
                                }
                            }
                        }
                        if mode == 2 && fits {
                            let (g2, refused) = agg.merge(heap, groups2, sl[at + header_bytes()..at + header_bytes() + payload_len], tree, pp[scan.p_max_groups()], pp[scan.p_max_distinct()], pp[scan.p_max_state()]);
                            groups2 = g2;
                            if refused != 0 {
                                fits = false;
                            }
                        }
                        if fits {
                            accepted = true;
                            // The range's effect, added.
                            if a[engine.k_ragged()] == 0 && g > 0 {
                                a[engine.k_first_row()] = before + agg.get_i64(sl, at + 32 + 8 * engine.k_first_row());
                                a[engine.k_first_line()] = lines_before + agg.get_i64(sl, at + 32 + 8 * engine.k_first_line());
                                a[engine.k_first_found()] = agg.get_i64(sl, at + 32 + 8 * engine.k_first_found());
                            }
                            a[engine.k_records()] = before + r;
                            a[engine.k_ragged()] = a[engine.k_ragged()] + g;
                            if mode == 1 && e > 0 {
                                let body = sl[at + header_bytes()..at + header_bytes() + payload_len];
                                if !as_csv && a[engine.k_emitted()] > 0 {
                                    rows2 = buffer.push(heap, rows2, byte_of(','));
                                }
                                rows2 = buffer.append(heap, rows2, body);
                                a[engine.k_emitted()] = a[engine.k_emitted()] + e;
                            }
                            lines_before = lines_before + l_lines(sl, at);
                            cur = agg.get_i64(sl, at + 8);
                        }
                    }
                }
                if accepted {
                    if as_csv {
                        var held = 0;
                        borrow rows2 as &rr in {
                            held = buffer.size(rr);
                        }
                        if held >= 65536 {
                            let (after, ok) = engine.flush(io, rows2);
                            rows2 = after;
                            if !ok {
                                a[engine.k_abort()] = 9;
                                a[engine.k_stop()] = 1;
                            }
                        }
                    }
                } else {
                    // Read it again, here, the way the sequential read does.
                    var again = true;
                    while again {
                        pp[scan.p_base_line()] = lines_before;
                        let (x2, l2, st, r2, s2, e2, k2, g2) = scan.scan_range(heap, file, pp, tree, cols, sel, cells, a, rows2, scratch2, escr2, kept2, groups2, cur, until);
                        rows2 = r2;
                        scratch2 = s2;
                        escr2 = e2;
                        kept2 = k2;
                        groups2 = g2;
                        lines_before = lines_before + l2;
                        cur = x2;
                        again = false;
                        if st == 1 {
                            let (after, ok) = engine.flush(io, rows2);
                            rows2 = after;
                            if !ok {
                                a[engine.k_abort()] = 9;
                                a[engine.k_stop()] = 1;
                            } else {
                                again = true;
                            }
                        }
                    }
                }
            }
            if a[engine.k_stop()] == 1 || a[engine.k_abort()] != 0 {
                going = false;
            }
            j = j + 1;
        }
        // A wave always reads at least the record at its start (its first range ends after it), so
        // this does not happen; but a loop that cannot be shown to end is one more reason to say so.
        if going && cur == wave_began && cur < size {
            pp[scan.p_base_line()] = lines_before;
            let (x3, l3, st3, r3, s3, e3, k3, g3) = scan.scan_range(heap, file, pp, tree, cols, sel, cells, a, rows2, scratch2, escr2, kept2, groups2, cur, size);
            rows2 = r3;
            scratch2 = s3;
            escr2 = e3;
            kept2 = k3;
            groups2 = g3;
            lines_before = lines_before + l3;
            cur = x3;
            if st3 == 1 {
                let (after, ok) = engine.flush(io, rows2);
                rows2 = after;
                if !ok {
                    a[engine.k_abort()] = 9;
                    a[engine.k_stop()] = 1;
                }
            } else if cur >= size || a[engine.k_stop()] == 1 || a[engine.k_abort()] != 0 {
                going = false;
            }
        }
    }
    unbox_slice(heap, wpar);
    unbox_slice(heap, slots);
    unbox_slice(heap, plan);
    return (rows2, scratch2, escr2, kept2, groups2);
}

fn l_lines[&s](sl: &s [byte], at: int) -> [] int {
    return agg.get_i64(sl, at + 16);
}
