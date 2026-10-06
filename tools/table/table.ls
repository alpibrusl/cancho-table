edition 5;

// `table` -- a CSV or TSV file as a table: its shape, or some of its columns.
//
//     table [--root DIR] [--delimiter ,|tab|;] [--max-rows N] [--max-line-bytes N]
//           [--format json|text] FILE                         the shape
//     table --select NAMES [--limit N] [--from N] [--max-bytes N]
//           [--format json|csv] [the same] FILE               some columns
//
// Design and measurements: `docs/select.md`. The reader is `reader.ls` (RFC 4180,
// one line at a time), what is written for a field `writer.ls`, and the reading
// of `--select` `plan.ls`; this file drives them and says what went wrong.
//
// * The first record is the header; every later non-blank record is a row.
// * RFC 4180: a field may be quoted, a quote inside it is doubled, a quoted
//   field may hold the delimiter, a newline or a CRLF; a leading UTF-8 byte
//   order mark is dropped. A quote that opens in the middle of an unquoted
//   field is text, as in most readers. A closing quote followed by anything
//   but the delimiter or the end of the record is `parse.csv-bad-quote`.
// * A blank line (nothing but an optional CR) between records is skipped.
//   A lone CR is not a line break.
// * A row with a number of fields other than the header's is
//   `parse.csv-ragged-row`, one error for the file naming the first such row;
//   the rest of the file is still read and counted, and a ragged row is never
//   among the rows a selection returns. A quote left open at the end of the
//   input is `parse.csv-unterminated-quote`.
// * `--max-rows` is a bound on work, not memory: reading stops at it. In JSON
//   the answer says `truncated: true`; as CSV there is nowhere to say it, so it
//   is `limit.too-many-rows`. `--max-line-bytes` is a bound on memory: a
//   physical line longer than it is `limit.line-too-long` (the file cannot be
//   read past it with any confidence about where its records end), and the
//   header, and with `--select` every record, which are kept while they are
//   used, may not hold more than that many bytes in all (`limit.header-too-large`,
//   `limit.record-too-large`).
//
// Memory is one 64 KiB chunk, the longest record, the header, one row of
// output and, as JSON, the page asked for (`--limit`, within `--max-bytes`).

import std.buffer;
import std.bytes;
import std.json;
import std.vec;
import plan;
import reader;
import toolbox.built;
import toolbox.cli;
import toolbox.describe;
import toolbox.fail;
import toolbox.limit;
import toolbox.lines;
import toolbox.out;
import toolbox.path;
import toolbox.place;
import toolbox.text;
import writer;

fn flag_table() -> [] &static [byte] {
    return "root||path|root||resolve FILE relative to this directory and refuse paths outside it;delimiter|d|text|none|,|the field separator: a comma, the word tab (or a tab character), or a semicolon;select||text|none||the columns to return, named by their header separated by commas (a comma or backslash in a name is written with a backslash before it), or by position as #3, and in the order given;limit||nat|none||with --select, the most rows returned (default 1000 as json, no limit as csv, ceiling 1000000);from||nat|none||with --select, the 0-based row to start at, as the next of a truncated answer says;max-bytes||nat|none|1048576|the most json rows returned hold, past it the answer is truncated with a next (ceiling 67108864);max-rows||nat|none|10000000|the most data rows read, past it the answer says truncated (ceiling 1000000000);max-line-bytes||nat|none|1048576|the longest line read and the most a kept record may hold, a longer one is limit.line-too-long (ceiling 16777216);format||choice:json/text/csv|none|json|json for a program, text for a person (the shape only), csv for the selected columns as csv";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "table", version: "0.2.0", summary: "A CSV or TSV file as a table, read in one bounded streaming pass over an RFC 4180 reader: its shape (header names, data rows, columns), or with --select some of its columns as json rows (a page, with a next cursor) or as csv. Every refusal is a rule: a ragged row, an unterminated quote, an unknown column, a line past the limit.", usage: "table [--root DIR] [--delimiter ,|tab|;] [--max-rows N] [--max-line-bytes N] [--format json|text] FILE | table --select NAMES [--limit N] [--from N] [--max-bytes N] [--format json|csv] [--root DIR] [--delimiter ,|tab|;] FILE", output: "document", schema: "table.v2", flags: flag_table(), operands: "FILE|path-read|1|1|the CSV file to read", rules: "args.unknown-flag;args.missing-value;args.bad-value;args.duplicate-flag;args.conflict;args.required-flag;args.missing-operand;args.too-many-operands;path.empty;path.dotdot;path.absolute;path.outside-root;path.too-long;path.symlink;io.not-found;io.not-a-directory;io.is-a-directory;io.permission-denied;io.read-failed;limit.line-too-long;limit.header-too-large;limit.record-too-large;limit.output-too-large;limit.too-many-rows;parse.csv-ragged-row;parse.csv-bad-quote;parse.csv-unterminated-quote;select.unknown-column;select.ambiguous-column", extra_rules: extra(), limits: "limit|1000|1000000;max-bytes|1048576|67108864;max-rows|10000000|1000000000;max-line-bytes|1048576|16777216", reversibility: "reversible-cheap", stdin: "no", guarantees: "deterministic;idempotent;bounded_memory" };
}

// The tool's own rules, beside the contract's catalogue: tag, exit code,
// repairable, summary.
fn extra() -> [] &static [byte] {
    return "limit.header-too-large|8|never|the header record holds more than --max-line-bytes bytes;limit.record-too-large|8|never|a record read for --select holds more than --max-line-bytes bytes;limit.output-too-large|8|never|the first row of a page is longer than --max-bytes;limit.too-many-rows|8|never|--format csv reached --max-rows with rows left unread;parse.csv-ragged-row|8|never|a row has a different number of fields than the header;parse.csv-bad-quote|8|never|a closing quote is followed by something other than the delimiter or the end of the record;parse.csv-unterminated-quote|8|never|a quoted field is still open at the end of the input;select.unknown-column|3|sometimes|a name or position in --select that is not a column of the header;select.ambiguous-column|8|never|a name in --select that is the name of more than one column";
}

fn built() -> [] describe.Built {
    return describe.Built { authority: built.authority(), schema: built.schema(), compiler: built.compiler() };
}

fn row_ceiling() -> [] int {
    return 1000000000;
}

fn line_ceiling() -> [] int {
    return 16777216;
}

fn flag_problem[&h](heap: &!h Heap, e: fail.Errors, rule: &static [byte], message: &static [byte], hint: &static [byte], flags: &static [byte]) -> [heap] fail.Errors {
    var w = fail.open(heap, rule, message, hint);
    w = fail.no_repair(heap, w);
    w = fail.detail_open(heap, w);
    w = json.put_key(heap, w, "flags");
    w = json.put_string(heap, w, flags);
    return fail.add(heap, e, w);
}

// The delimiter byte `--delimiter` names, or -1.
fn delimiter_of[&t](given: &t [byte]) -> [] int {
    if bytes.equal(given, "tab") || len(given) == 1 && int_of(given[0]) == 9 {
        return 9;
    }
    if len(given) == 1 && (int_of(given[0]) == ',' || int_of(given[0]) == ';') {
        return int_of(given[0]);
    }
    return 0 - 1;
}

// What a read counted. The tally lives in an array of ints while the read
// runs (`k_*` say where) and is copied here after.
//
// `abort` is why it stopped before the end of the input: 0 it did not, 1 a bad
// quote, 2 a quote left open, 3 a header past its limit, 4 a line past the
// limit, 5 a name that is no column, 6 a name that is several, 7 a record past
// its limit, 8 a first row past the budget, 9 standard output refused a write;
// `abort_line` is the line it is about.
struct Counts {
    columns: int,
    records: int,
    ragged: int,
    first_row: int,
    first_line: int,
    first_found: int,
    capped: bool,
    abort: int,
    abort_line: int,
    long_length: int,
    emitted: int,
    more: bool,
    next: int,
    bad_name: int,
}

fn k_records() -> [] int {
    return 0;
}

fn k_ragged() -> [] int {
    return 1;
}

fn k_first_row() -> [] int {
    return 2;
}

fn k_first_line() -> [] int {
    return 3;
}

fn k_first_found() -> [] int {
    return 4;
}

fn k_capped() -> [] int {
    return 5;
}

fn k_abort() -> [] int {
    return 6;
}

fn k_abort_line() -> [] int {
    return 7;
}

fn k_long() -> [] int {
    return 8;
}

fn k_emitted() -> [] int {
    return 9;
}

fn k_more() -> [] int {
    return 10;
}

fn k_next() -> [] int {
    return 11;
}

fn k_columns() -> [] int {
    return 12;
}

fn k_bad_name() -> [] int {
    return 13;
}

fn k_stop() -> [] int {
    return 14;
}

fn k_size() -> [] int {
    return 16;
}

// One more data record, number `opened`'s line it began on, of `found` fields:
// counted, and answers whether it is a row to hand on (not ragged, and not
// before `from`).
fn count_row[&a](a: &!a [int], found: int, opened: int, from: int) -> [] bool {
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
fn flush[&i](io: &!i Io, pending: buffer.Buffer) -> [io_write] (buffer.Buffer, bool) {
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
// `cells`, as csv into `rows` (written out once a block of it is pending) or as
// json into `rows`, through `scratch` so that a row that does not fit the
// budget is not half in. Updates the tally: `emitted`, and `stop` with
// `more` and `next` when the page ends here or `abort` when it cannot.
fn emit_row[&h, &i, &d, &c, &s, &a](heap: &!h Heap, io: &!i Io, rows: buffer.Buffer, scratch: buffer.Buffer, record: &d [byte], cells: &c [int], sel: &s [int], picked: int, delim: int, as_csv: bool, budget: int, a: &!a [int]) -> [heap, io_write] (buffer.Buffer, buffer.Buffer) {
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
        if held >= 65536 {
            let (after, ok) = flush(io, pending);
            pending = after;
            if !ok {
                a[k_abort()] = 9;
                a[k_stop()] = 1;
            }
        }
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

// Read the open file. With `want` the rows selected by `toks` (NAMES, parsed by
// `plan.parse`) are written: as csv straight to standard output, as json into
// the buffer answered third (the first is the header's `columns` array text).
// Answers what was counted, those two buffers, the header's names and where
// each ends, and the errors.
fn read_file[&h, &g, &p, &f, &s, &i, &t, &k, &j](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, file: &!f File, shown: &s [byte], io: &!i Io, delim: int, want: bool, as_csv: bool, from: int, limit: int, budget: int, toks: &t buffer.Buffer, tends: &k vec.Vec[int], tkinds: &j vec.Vec[int], names_at: int, errs: fail.Errors) -> [heap, args, file_read, io_write] (Counts, buffer.Buffer, buffer.Buffer, buffer.Buffer, vec.Vec[int], fail.Errors) {
    let table = flag_table();
    let cap = cli.nat(args, parsed, table, "max-line-bytes");
    let most = cli.nat(args, parsed, table, "max-rows");
    var e = errs;
    var names = buffer.empty(heap, 1);
    var hends = vec.empty(heap, 1, 0);
    var head = buffer.empty(heap, 1);
    var rows = buffer.empty(heap, 1);
    var scratch = buffer.empty(heap, 1);
    var rec = buffer.empty(heap, 256);
    var cells = box_slice(heap, 3, 0);
    var sel = box_slice(heap, 1, 0);
    var tally = box_slice(heap, k_size(), 0);
    var picked = 0;
    var header = false;
    var quoted = false;
    var seps = 0;
    var opened = 0;
    var r = lines.start(heap, cap);
    var going = true;
    var c = Counts { columns: 0, records: 0, ragged: 0, first_row: 0, first_line: 0, first_found: 0, capped: false, abort: 0, abort_line: 0, long_length: 0, emitted: 0, more: false, next: 0, bad_name: 0 };
    borrow mut tally as &!tw in {
        let a = contents(tw);
        while going {
            let (stepped, status) = lines.next(heap, r);
            r = stepped;
            if status == lines.need() {
                r = lines.fill_file(r, file);
            } else if status == lines.done() {
                going = false;
            } else if status == lines.long() {
                a[k_abort()] = 4;
                borrow r as &rr in {
                    a[k_abort_line()] = lines.number(rr);
                    a[k_long()] = lines.length(rr);
                }
                going = false;
            } else {
                borrow r as &rr in {
                    let number = lines.number(rr);
                    var line = lines.text(rr);
                    // A byte order mark belongs to the file, not to the first name.
                    if number == 1 && len(line) >= 3 && int_of(line[0]) == 239 && int_of(line[1]) == 187 && int_of(line[2]) == 191 {
                        line = line[3..len(line)];
                    }
                    let whole = line;
                    if len(line) > 0 && int_of(line[len(line) - 1]) == 13 {
                        line = line[0..len(line) - 1];
                    }
                    if !quoted && len(line) == 0 {
                        // A blank line between records is not a record.
                    } else if header && !quoted && a[k_records()] >= most {
                        // A record past the bound: not read, so not judged.
                        a[k_capped()] = 1;
                        going = false;
                    } else if header && !quoted && a[k_records()] >= from && a[k_emitted()] >= limit {
                        // The page is full and there is another record.
                        a[k_more()] = 1;
                        a[k_next()] = a[k_records()];
                        going = false;
                    } else if header && !quoted {
                        // A data record begins; most are whole on this line.
                        var inside = false;
                        var found = 0;
                        var bad = false;
                        if want {
                            borrow mut cells as &!cw in {
                                let (n, open, wrong) = reader.fields(line, delim, contents(cw), a[k_columns()]);
                                found = n;
                                inside = open;
                                bad = wrong;
                            }
                        } else {
                            let (open, n, wrong) = reader.scan(line, delim, false, 0);
                            found = n + 1;
                            inside = open;
                            seps = n;
                            bad = wrong;
                        }
                        if bad {
                            a[k_abort()] = 1;
                            a[k_abort_line()] = number;
                            going = false;
                        } else if inside {
                            quoted = true;
                            opened = number;
                            borrow mut rec as &!rw in {
                                buffer.clear(rw);
                            }
                            if want {
                                rec = buffer.append(heap, rec, whole);
                                rec = buffer.push(heap, rec, byte_of(10));
                            }
                        } else if count_row(a, found, number, from) && want {
                            borrow cells as &cr in {
                                borrow sel as &sr in {
                                    let (more_rows, more_scratch) = emit_row(heap, io, rows, scratch, line, contents(cr), contents(sr), picked, delim, as_csv, budget, a);
                                    rows = more_rows;
                                    scratch = more_scratch;
                                }
                            }
                        }
                    } else {
                        // The header, or the rest of a record that has quotes open: kept.
                        if !quoted {
                            opened = number;
                            borrow mut rec as &!rw in {
                                buffer.clear(rw);
                            }
                            seps = 0;
                        }
                        let (inside, n, bad) = reader.scan(line, delim, quoted, seps);
                        seps = n;
                        if !header || want {
                            rec = buffer.append(heap, rec, whole);
                        }
                        var held = 0;
                        borrow rec as &rw in {
                            held = buffer.size(rw);
                        }
                        if bad {
                            a[k_abort()] = 1;
                            a[k_abort_line()] = number;
                            going = false;
                        } else if inside {
                            quoted = true;
                            if !header || want {
                                rec = buffer.push(heap, rec, byte_of(10));
                            }
                            if held + 1 > cap {
                                a[k_abort()] = 3;
                                if header {
                                    a[k_abort()] = 7;
                                }
                                a[k_abort_line()] = opened;
                                going = false;
                            }
                        } else {
                            quoted = false;
                            seps = 0;
                            if !header {
                                buffer.drop(heap, names);
                                vec.drop(heap, hends);
                                borrow rec as &rw in {
                                    let (nm, en) = reader.split_header(heap, buffer.bytes(rw), delim);
                                    names = nm;
                                    hends = en;
                                }
                                header = true;
                                borrow hends as &er in {
                                    a[k_columns()] = vec.size(er);
                                }
                                if want {
                                    picked = vec.size(tends);
                                    unbox_slice(heap, sel);
                                    sel = box_slice(heap, picked + 1, 0);
                                    unbox_slice(heap, cells);
                                    cells = box_slice(heap, 3 * (a[k_columns()] + 1), 0);
                                    var status = 0;
                                    borrow names as &nr in {
                                        borrow hends as &er in {
                                            borrow mut sel as &!sw in {
                                                let found = plan.resolve(heap, nr, er, toks, tends, tkinds, contents(sw));
                                                status = found.status;
                                                a[k_bad_name()] = found.at;
                                            }
                                        }
                                    }
                                    if status != 0 {
                                        a[k_abort()] = 4 + status;
                                        going = false;
                                    } else {
                                        // The header of the answer.
                                        borrow names as &nr in {
                                            borrow hends as &er in {
                                                borrow sel as &sr in {
                                                    var column = 0;
                                                    var m = 0;
                                                    var line_out = buffer.empty(heap, 64);
                                                    head = buffer.push(heap, head, byte_of('['));
                                                    while m < picked {
                                                        column = contents(sr)[m];
                                                        var begin = 0;
                                                        if column > 0 {
                                                            begin = vec.get(er, column - 1);
                                                        }
                                                        let name = buffer.bytes(nr)[begin..vec.get(er, column)];
                                                        if m > 0 {
                                                            head = buffer.push(heap, head, byte_of(','));
                                                            line_out = buffer.push(heap, line_out, byte_of(delim));
                                                        }
                                                        head = text.append_json(heap, head, name);
                                                        line_out = writer.csv_value(heap, line_out, name, delim);
                                                        m = m + 1;
                                                    }
                                                    head = buffer.push(heap, head, byte_of(']'));
                                                    if as_csv {
                                                        var empty_name = false;
                                                        borrow line_out as &lo in {
                                                            empty_name = picked == 1 && buffer.size(lo) == 0;
                                                        }
                                                        if empty_name {
                                                            // One empty name is `""`: a blank line is no record.
                                                            line_out = buffer.append(heap, line_out, "\"\"");
                                                        }
                                                        line_out = buffer.push(heap, line_out, byte_of(10));
                                                        borrow line_out as &lr in {
                                                            rows = buffer.append(heap, rows, buffer.bytes(lr));
                                                        }
                                                    }
                                                    buffer.drop(heap, line_out);
                                                }
                                            }
                                        }
                                    }
                                }
                            } else {
                                // A data record that spanned lines, whole in `rec`.
                                var record_len = 0;
                                borrow rec as &rw in {
                                    record_len = buffer.size(rw);
                                    var record = buffer.bytes(rw);
                                    if record_len > 0 && int_of(record[record_len - 1]) == 13 {
                                        record = record[0..record_len - 1];
                                    }
                                    var found = n + 1;
                                    var wrong = false;
                                    if want {
                                        borrow mut cells as &!cw in {
                                            let (n, open, bad_quote) = reader.fields(record, delim, contents(cw), a[k_columns()]);
                                            found = n;
                                            wrong = bad_quote || open;
                                        }
                                    }
                                    if wrong {
                                        a[k_abort()] = 1;
                                        a[k_abort_line()] = opened;
                                        going = false;
                                    } else if count_row(a, found, opened, from) && want {
                                        borrow cells as &cr in {
                                            borrow sel as &sr in {
                                                let (more_rows, more_scratch) = emit_row(heap, io, rows, scratch, record, contents(cr), contents(sr), picked, delim, as_csv, budget, a);
                                                rows = more_rows;
                                                scratch = more_scratch;
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                if a[k_stop()] == 1 {
                    going = false;
                }
            }
        }
        // No header at all: no column to name.
        if want && !header && a[k_abort()] == 0 {
            a[k_abort()] = 5;
        }
        // The end of the input with a quote open: not a record.
        if quoted && a[k_abort()] == 0 {
            a[k_abort()] = 2;
            a[k_abort_line()] = opened;
        }
        c = Counts { columns: a[k_columns()], records: a[k_records()], ragged: a[k_ragged()], first_row: a[k_first_row()], first_line: a[k_first_line()], first_found: a[k_first_found()], capped: a[k_capped()] == 1, abort: a[k_abort()], abort_line: a[k_abort_line()], long_length: a[k_long()], emitted: a[k_emitted()], more: a[k_more()] == 1, next: a[k_next()], bad_name: a[k_bad_name()] };
    }
    unbox_slice(heap, tally);
    unbox_slice(heap, cells);
    unbox_slice(heap, sel);
    buffer.drop(heap, rec);
    buffer.drop(heap, scratch);
    var errno = 0;
    borrow r as &rr in {
        errno = lines.failed(rr);
    }
    lines.drop(heap, r);
    // What csv still had pending, written now unless the stream already failed.
    if as_csv && c.abort != 9 {
        let (after, ok) = flush(io, rows);
        rows = after;
        if !ok {
            c = Counts { columns: c.columns, records: c.records, ragged: c.ragged, first_row: c.first_row, first_line: c.first_line, first_found: c.first_found, capped: c.capped, abort: 9, abort_line: 0, long_length: 0, emitted: c.emitted, more: c.more, next: c.next, bad_name: c.bad_name };
        }
    }
    if errno != 0 {
        e = fail.io_error(heap, e, errno, false, shown);
    } else if c.abort == 4 {
        let over = limit.more(limit.none(), c.abort_line, c.long_length);
        e = limit.too_long(heap, e, args, parsed, table, shown, over, cap, line_ceiling(), "a line is longer than --max-line-bytes; the file was not read past it");
    } else if c.abort == 3 || c.abort == 7 {
        var rule = "limit.header-too-large";
        var what = "the header record holds more than --max-line-bytes bytes";
        if c.abort == 7 {
            rule = "limit.record-too-large";
            what = "a record holds more than --max-line-bytes bytes";
        }
        var w = fail.open_in(heap, extra(), rule, what, "raise --max-line-bytes, up to the ceiling introspect names");
        w = fail.repair_none(heap, w, "how large the record is was not read to the end");
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "path");
        w = text.put(heap, w, shown);
        w = json.put_key(heap, w, "line");
        w = json.put_int(heap, w, c.abort_line);
        w = json.put_key(heap, w, "limit");
        w = json.put_int(heap, w, cap);
        e = fail.add(heap, e, w);
    } else if c.abort == 2 {
        var w = fail.open_in(heap, extra(), "parse.csv-unterminated-quote", "a quoted field is open at the end of the input", "close the quote, or double the quotes that are text");
        w = fail.repair_none(heap, w, "which quote was meant to close is not known");
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "path");
        w = text.put(heap, w, shown);
        w = json.put_key(heap, w, "line");
        w = json.put_int(heap, w, c.abort_line);
        e = fail.add(heap, e, w);
    } else if c.abort == 1 {
        var w = fail.open_in(heap, extra(), "parse.csv-bad-quote", "a closing quote is followed by something other than the delimiter or the end of the record", "double a quote that is text, or put the delimiter after the closing quote");
        w = fail.repair_none(heap, w, "what the field was meant to hold is not known");
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "path");
        w = text.put(heap, w, shown);
        w = json.put_key(heap, w, "line");
        w = json.put_int(heap, w, c.abort_line);
        e = fail.add(heap, e, w);
    } else if c.abort == 5 || c.abort == 6 {
        var rule = "select.unknown-column";
        var what = "--select names a column the header does not have";
        var hint = "pick from detail.available";
        if c.abort == 6 {
            rule = "select.ambiguous-column";
            what = "--select names a column that the header has more than once";
            hint = "name it by position, as #3 for the third column";
        }
        borrow names as &nr in {
            borrow hends as &er3 in {
                e = plan.refusal(heap, e, extra(), rule, what, hint, args, names_at, nr, er3, toks, tends, tkinds, c.bad_name, shown);
            }
        }
    } else if c.abort == 8 {
        var w = fail.open_in(heap, extra(), "limit.output-too-large", "the first row of the page is longer than --max-bytes", "raise --max-bytes, up to the ceiling introspect names, or select fewer columns");
        w = fail.repair_none(heap, w, "how long the row is was not measured past the budget");
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "path");
        w = text.put(heap, w, shown);
        w = json.put_key(heap, w, "limit");
        w = json.put_int(heap, w, budget);
        e = fail.add(heap, e, w);
    } else if as_csv && c.capped {
        var w = fail.open_in(heap, extra(), "limit.too-many-rows", "--max-rows data rows were written and there are more", "raise --max-rows, up to the ceiling introspect names, or page with --limit and --from");
        w = fail.repair_none(heap, w, "how many rows the file has is not known until it is read");
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "path");
        w = text.put(heap, w, shown);
        w = json.put_key(heap, w, "limit");
        w = json.put_int(heap, w, most);
        e = fail.add(heap, e, w);
    } else if c.ragged > 0 {
        var w = fail.open_in(heap, extra(), "parse.csv-ragged-row", "a row has a different number of fields than the header", "make every row as wide as the header, quoting fields that hold the delimiter");
        w = fail.repair_none(heap, w, "which fields a short or long row is missing or has too many of is not known");
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "path");
        w = text.put(heap, w, shown);
        w = json.put_key(heap, w, "ragged_rows");
        w = json.put_int(heap, w, c.ragged);
        w = json.put_key(heap, w, "first_row");
        w = json.put_int(heap, w, c.first_row);
        w = json.put_key(heap, w, "first_line");
        w = json.put_int(heap, w, c.first_line);
        w = json.put_key(heap, w, "expected");
        w = json.put_int(heap, w, c.columns);
        w = json.put_key(heap, w, "found");
        w = json.put_int(heap, w, c.first_found);
        e = fail.add(heap, e, w);
    }
    return (c, head, rows, names, hends, e);
}

// The answer for the shape: `{"headers", "column_count", "row_count",
// "truncated"}` and its text form.
fn report_shape[&h, &n, &d](heap: &!h Heap, c: Counts, names: &n buffer.Buffer, ends: &d vec.Vec[int]) -> [heap] (buffer.Buffer, buffer.Buffer) {
    var plain = buffer.empty(heap, 256 + buffer.size(names));
    var o = buffer.empty(heap, 256 + buffer.size(names) * 2);
    o = buffer.append(heap, o, "{\"headers\":[");
    plain = buffer.append(heap, plain, "rows\t");
    plain = buffer.push_nat(heap, plain, c.records);
    plain = buffer.append(heap, plain, "\ncolumns\t");
    plain = buffer.push_nat(heap, plain, c.columns);
    plain = buffer.append(heap, plain, "\ntruncated\t");
    if c.capped {
        plain = buffer.append(heap, plain, "true");
    } else {
        plain = buffer.append(heap, plain, "false");
    }
    plain = buffer.append(heap, plain, "\nheader");
    var i = 0;
    var from = 0;
    while i < vec.size(ends) && c.columns > 0 {
        let to = vec.get(ends, i);
        let name = buffer.bytes(names)[from..to];
        if i > 0 {
            o = buffer.push(heap, o, byte_of(','));
        }
        o = text.append_json(heap, o, name);
        plain = buffer.push(heap, plain, byte_of(9));
        plain = buffer.append(heap, plain, name);
        from = to;
        i = i + 1;
    }
    plain = buffer.push(heap, plain, byte_of(10));
    o = buffer.append(heap, o, "],\"column_count\":");
    o = buffer.push_nat(heap, o, c.columns);
    o = buffer.append(heap, o, ",\"row_count\":");
    o = buffer.push_nat(heap, o, c.records);
    if c.capped {
        o = buffer.append(heap, o, ",\"truncated\":true}");
    } else {
        o = buffer.append(heap, o, ",\"truncated\":false}");
    }
    return (o, plain);
}

// The answer for a selection as json: `{"columns", "rows", "row_count",
// "truncated", "next"}`.
fn report_select[&h, &a, &b](heap: &!h Heap, c: Counts, head: &a buffer.Buffer, rows: &b buffer.Buffer) -> [heap] buffer.Buffer {
    var o = buffer.empty(heap, 128 + buffer.size(head) + buffer.size(rows));
    o = buffer.append(heap, o, "{\"columns\":");
    o = buffer.append(heap, o, buffer.bytes(head));
    o = buffer.append(heap, o, ",\"rows\":[");
    o = buffer.append(heap, o, buffer.bytes(rows));
    o = buffer.append(heap, o, "],\"row_count\":");
    o = buffer.push_nat(heap, o, c.emitted);
    if c.more || c.capped {
        o = buffer.append(heap, o, ",\"truncated\":true,\"next\":");
    } else {
        o = buffer.append(heap, o, ",\"truncated\":false,\"next\":");
    }
    if c.more {
        o = buffer.append(heap, o, "{\"from\":");
        o = buffer.push_nat(heap, o, c.next);
        o = buffer.append(heap, o, "}}");
    } else {
        o = buffer.append(heap, o, "null}");
    }
    return o;
}

fn body[&h, &g, &p, &f, &i](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, errs: fail.Errors) -> [heap, args, fs_read(""), dir_read, file_read, io_write, err_write] int {
    let table = flag_table();
    var e = errs;
    let form = cli.text(args, parsed, table, "format");
    let text_mode = bytes.equal(form, "text");
    let as_csv = bytes.equal(form, "csv");
    let want = cli.has(parsed, table, "select");
    let delim = delimiter_of(cli.text(args, parsed, table, "delimiter"));
    if delim < 0 {
        e = flag_problem(heap, e, "args.bad-value", "--delimiter is a comma, tab or a semicolon", "--delimiter tab", "--delimiter");
    }
    if cli.nat(args, parsed, table, "max-rows") > row_ceiling() {
        e = flag_problem(heap, e, "args.bad-value", "--max-rows is above the ceiling", "1000000000 is the most this tool counts", "--max-rows");
    }
    if cli.nat(args, parsed, table, "max-line-bytes") > line_ceiling() {
        e = flag_problem(heap, e, "args.bad-value", "--max-line-bytes is above the ceiling", "16777216 is the most this tool holds", "--max-line-bytes");
    }
    var budget = cli.nat(args, parsed, table, "max-bytes");
    if budget < 1 || budget > 67108864 {
        e = flag_problem(heap, e, "args.bad-value", "--max-bytes is from 1 to the ceiling", "67108864 is the most a page holds", "--max-bytes");
    }
    var limit = 1000000000000000;
    if !as_csv {
        limit = 1000;
    }
    var from = 0;
    if cli.has(parsed, table, "limit") {
        limit = cli.nat(args, parsed, table, "limit");
        if limit < 1 || limit > 1000000 {
            e = flag_problem(heap, e, "args.bad-value", "--limit is from 1 to 1000000", "--limit 1000", "--limit");
        }
    }
    if cli.has(parsed, table, "from") {
        from = cli.nat(args, parsed, table, "from");
    }
    if !want {
        if as_csv {
            e = flag_problem(heap, e, "args.required-flag", "--format csv writes the selected columns, so it needs --select", "--select NAMES", "--format --select");
        }
        if cli.has(parsed, table, "limit") || cli.has(parsed, table, "from") {
            e = flag_problem(heap, e, "args.required-flag", "--limit and --from page a selection, so they need --select", "--select NAMES", "--limit --from --select");
        }
    } else if text_mode {
        e = flag_problem(heap, e, "args.conflict", "--format text is for the shape; a selection is json or csv", "--format csv", "--select --format");
    }
    var given = "";
    if want {
        given = cli.text(args, parsed, table, "select");
    }
    let (toks, tends, tkinds, listed) = plan.parse(heap, given);
    if want && !listed {
        e = flag_problem(heap, e, "args.bad-value", "--select is names separated by commas, and a backslash escapes only a comma, a backslash or a #", "--select 'a,b\\,c'", "--select");
    }
    var named = 0;
    borrow tends as &ter in {
        named = vec.size(ter);
    }
    if named > plan.most_names() {
        e = flag_problem(heap, e, "args.bad-value", "--select names more columns than the ceiling", "4096 is the most one call selects", "--select");
    }
    let (root, after_root) = path.root(heap, args, cli.text(args, parsed, table, "root"), cli.value_index(parsed, table, "root"), e);
    e = after_root;
    if cli.operand_count(parsed) == 0 {
        e = flag_problem(heap, e, "args.missing-operand", "table takes the FILE to read", "table FILE", "FILE");
    } else if cli.operand_count(parsed) > 1 {
        e = flag_problem(heap, e, "args.too-many-operands", "table takes one FILE", "read one file per call", "FILE");
    }
    var payload = buffer.empty(heap, 1);
    var plain = buffer.empty(heap, 1);
    var streamed = false;
    var refused = false;
    borrow e as &er in {
        refused = fail.count(er) > 0;
    }
    var names_at = -1;
    if want && cli.value_is_whole(parsed, table, "select") {
        names_at = cli.value_index(parsed, table, "select");
    }
    if !refused {
        borrow root as &rr in {
            let (target, checked) = path.operand(heap, args, buffer.bytes(rr), cli.operand_index(parsed, 0), e);
            e = checked;
            borrow target as &tp in {
                if path.ok(tp) {
                    match place.open_operand(fs, buffer.bytes(rr), path.shown(tp), path.full(tp)) {
                        Opened::Failed(reason) => {
                            e = fail.io_error(heap, e, reason, false, path.shown(tp));
                        }
                        Opened::Ok(opened) => {
                            var file = opened;
                            borrow mut file as &!handle in {
                                var wrote = 0;
                                borrow toks as &tr in {
                                    borrow tends as &ter in {
                                        borrow tkinds as &tkr in {
                                            let (c, head, rows, names, hends, after) = read_file(heap, args, parsed, handle, path.shown(tp), io, delim, want, as_csv, from, limit, budget, tr, ter, tkr, names_at, e);
                                            e = after;
                                            // What was read is the answer unless the read had to
                                            // stop short of the end: a ragged row is an error
                                            // about rows that were all counted.
                                            streamed = as_csv;
                                            wrote = c.abort;
                                            if c.abort == 0 && !as_csv {
                                                buffer.drop(heap, payload);
                                                buffer.drop(heap, plain);
                                                if want {
                                                    borrow head as &hr in {
                                                        borrow rows as &wr in {
                                                            payload = report_select(heap, c, hr, wr);
                                                        }
                                                    }
                                                    plain = buffer.empty(heap, 1);
                                                } else {
                                                    borrow names as &nr in {
                                                        borrow hends as &er2 in {
                                                            let (d, t) = report_shape(heap, c, nr, er2);
                                                            payload = d;
                                                            plain = t;
                                                        }
                                                    }
                                                }
                                            }
                                            buffer.drop(heap, head);
                                            buffer.drop(heap, rows);
                                            buffer.drop(heap, names);
                                            vec.drop(heap, hends);
                                        }
                                    }
                                }
                                if wrote == 9 {
                                    streamed = false;
                                    e = fail.io_error(heap, e, 5, true, "standard output");
                                }
                            }
                            file_close(file);
                        }
                    }
                }
            }
            path.drop(heap, target);
        }
    }
    buffer.drop(heap, root);
    buffer.drop(heap, toks);
    vec.drop(heap, tends);
    vec.drop(heap, tkinds);
    var status = 0;
    if streamed || as_csv {
        // csv has no document: its errors are sentences on standard error and
        // the exit status says whether it is whole.
        borrow e as &er in {
            if fail.count(er) > 0 {
                out.say_errors(io, "table", er);
                status = fail.exit_code(er);
            }
        }
        if status == 0 && !out.flushed(io) {
            out.write_failed(io, "table");
            status = 1;
        }
    } else {
        borrow e as &er in {
            borrow payload as &d in {
                borrow plain as &t in {
                    status = out.respond(heap, io, "table", "table.v2", "0.2.0", buffer.bytes(d), "", er, text_mode, buffer.bytes(t), 0);
                }
            }
        }
    }
    buffer.drop(heap, payload);
    buffer.drop(heap, plain);
    fail.drop(heap, e);
    return status;
}

fn run[&h, &g, &f, &i](heap: &!h Heap, args: &g Args, fs: &f Fs(""), io: &!i Io) -> [heap, args, fs_read(""), dir_read, file_read, io_write, err_write] int {
    let which = cli.subcommand(args);
    if which != 0 {
        return describe.answer(heap, io, which, tool(), built());
    }
    let (parsed, e) = cli.parse(heap, args, flag_table(), fail.empty_in(heap, extra()));
    var status = 0;
    borrow parsed as &p in {
        status = body(heap, args, p, fs, io, e);
    }
    cli.drop(heap, parsed);
    return status;
}

fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock } = split(world);
    // No foreign code, no network, no clock.
    release(ffi);
    release(net);
    release(clock);
    var status = 0;
    borrow mut heap as &!h in {
        borrow args as &g in {
            borrow fs as &f in {
                borrow mut io as &!i in {
                    status = run(h, g, f, i);
                }
            }
        }
    }
    release(heap);
    release(args);
    release(fs);
    release(io);
    return status;
}
