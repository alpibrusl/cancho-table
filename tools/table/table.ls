edition 5;

// `table` -- the shape of a CSV or TSV file: its header names, its number of
// data rows and its number of columns, in one bounded, streaming pass.
//
//     table [--root DIR] [--delimiter ,|tab|;] [--max-rows N]
//           [--max-line-bytes N] [--format json|text] FILE
//
// This is the skeleton of the tool `docs/next-tools.md` section 5 of
// lexsys-tools designs: one operation, the one every later operation (select,
// filter, group, sort) needs first, a reader that is right about RFC 4180.
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
//   the rest of the file is still read and counted. A quote left open at the
//   end of the input is `parse.csv-unterminated-quote`.
// * `--max-rows` is a bound on work, not memory: the count stops at it and the
//   answer says `truncated: true`, as `peek` does for its budget. It is not
//   an error. `--max-line-bytes` is a bound on memory: a physical line longer
//   than it is `limit.line-too-long` (the file cannot be read past it with
//   any confidence about where its records end, so the read stops there), and
//   the header record, which is kept, may not hold more than that many bytes
//   in all (`limit.header-too-large`).
//
// Memory is one 64 KiB chunk, the longest line, and the header. The reader is
// `toolbox.lines`; the parse state is a quote flag and a field count that
// pass from one line to the next, so a quoted field that spans a million lines
// costs what one line costs.

import std.buffer;
import std.bytes;
import std.json;
import std.vec;
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

fn flag_table() -> [] &static [byte] {
    return "root||path|root||resolve FILE relative to this directory and refuse paths outside it;delimiter|d|text|none|,|the field separator: a comma, the word tab (or a tab character), or a semicolon;max-rows||nat|none|10000000|the most data rows counted, past it the answer says truncated (ceiling 1000000000);max-line-bytes||nat|none|1048576|the longest line read and the most the header may hold, a longer one is limit.line-too-long (ceiling 16777216);format||choice:json/text|none|json|json for a program, text for a person";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "table", version: "0.1.0", summary: "The shape of a CSV or TSV file: header names, data rows and columns, from one bounded streaming pass over an RFC 4180 reader. A ragged row, an unterminated quote and a line past the limit are errors with a rule; a row count past --max-rows is truncated.", usage: "table [--root DIR] [--delimiter ,|tab|;] [--max-rows N] [--max-line-bytes N] [--format json|text] FILE", output: "document", schema: "table.v1", flags: flag_table(), operands: "FILE|path-read|1|1|the CSV file to describe", rules: "args.unknown-flag;args.missing-value;args.bad-value;args.duplicate-flag;args.missing-operand;args.too-many-operands;path.empty;path.dotdot;path.absolute;path.outside-root;path.too-long;path.symlink;io.not-found;io.not-a-directory;io.is-a-directory;io.permission-denied;io.read-failed;limit.line-too-long;limit.header-too-large;parse.csv-ragged-row;parse.csv-bad-quote;parse.csv-unterminated-quote", limits: "max-rows|10000000|1000000000;max-line-bytes|1048576|16777216", reversibility: "reversible-cheap", stdin: "no", guarantees: "deterministic;idempotent;bounded_memory" };
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

// What the pass found. `abort` is why it stopped before the end of the input:
// 0 it did not, 1 a bad quote, 2 a quote left open, 3 a header past its limit,
// 4 a line past the limit; `abort_line` is the line it is about.
struct Shape {
    columns: int,
    rows: int,
    ragged: int,
    first_row: int,
    first_line: int,
    first_found: int,
    truncated: bool,
    abort: int,
    abort_line: int,
    long_length: int,
}

// One physical line of a record, from the state the previous line left.
// `quoted` is whether a quoted field is open, `seps` the delimiters of the
// record outside quotes so far. Answers the same two at the end of the line
// and whether a quote closed and was followed by something it may not be.
//
// The line is walked by its quotes, not its bytes: between two quotes the
// delimiters are counted with `count_byte`, so a row whose one quoted field
// is the only quote costs three searches, not one step per byte.
fn scan[&l](line: &l [byte], delim: int, quoted: bool, seps: int) -> [] (bool, int, bool) {
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
fn split_header[&h, &r](heap: &!h Heap, raw: &r [byte], delim: int) -> [heap] (buffer.Buffer, vec.Vec[int]) {
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

// Read the open file: the header, then the shape of everything after it.
fn read_file[&h, &g, &p, &f, &s](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, file: &!f File, shown: &s [byte], delim: int, errs: fail.Errors) -> [heap, args, file_read] (Shape, buffer.Buffer, vec.Vec[int], fail.Errors) {
    let table = flag_table();
    let cap = cli.nat(args, parsed, table, "max-line-bytes");
    let most = cli.nat(args, parsed, table, "max-rows");
    var e = errs;
    var s = Shape { columns: 0, rows: 0, ragged: 0, first_row: 0, first_line: 0, first_found: 0, truncated: false, abort: 0, abort_line: 0, long_length: 0 };
    var names = buffer.empty(heap, 1);
    var ends = vec.empty(heap, 1, 0);
    var raw = buffer.empty(heap, 256);
    var header = false;
    var quoted = false;
    var seps = 0;
    var opened = 0;
    var r = lines.start(heap, cap);
    var going = true;
    while going {
        let (stepped, status) = lines.next(heap, r);
        r = stepped;
        if status == lines.need() {
            r = lines.fill_file(r, file);
        } else if status == lines.done() {
            going = false;
        } else if status == lines.long() {
            borrow r as &rr in {
                s = Shape { columns: s.columns, rows: s.rows, ragged: s.ragged, first_row: s.first_row, first_line: s.first_line, first_found: s.first_found, truncated: s.truncated, abort: 4, abort_line: lines.number(rr), long_length: lines.length(rr) };
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
                } else if !quoted && header && s.rows >= most {
                    // A record past the bound: not read, so not judged.
                    s = Shape { columns: s.columns, rows: s.rows, ragged: s.ragged, first_row: s.first_row, first_line: s.first_line, first_found: s.first_found, truncated: true, abort: 0, abort_line: 0, long_length: 0 };
                    going = false;
                } else {
                    if !quoted {
                        opened = number;
                    }
                    let (inside, n, bad) = scan(line, delim, quoted, seps);
                    if !header {
                        raw = buffer.append(heap, raw, whole);
                    }
                    if bad {
                        s = Shape { columns: s.columns, rows: s.rows, ragged: s.ragged, first_row: s.first_row, first_line: s.first_line, first_found: s.first_found, truncated: s.truncated, abort: 1, abort_line: number, long_length: 0 };
                        going = false;
                    } else if inside {
                        quoted = true;
                        seps = n;
                        if !header {
                            raw = buffer.push(heap, raw, byte_of(10));
                            var held = 0;
                            borrow raw as &rw in {
                                held = buffer.size(rw);
                            }
                            if held > cap {
                                s = Shape { columns: s.columns, rows: s.rows, ragged: s.ragged, first_row: s.first_row, first_line: s.first_line, first_found: s.first_found, truncated: s.truncated, abort: 3, abort_line: opened, long_length: 0 };
                                going = false;
                            }
                        }
                    } else {
                        quoted = false;
                        seps = 0;
                        if !header {
                            buffer.drop(heap, names);
                            vec.drop(heap, ends);
                            borrow raw as &rw in {
                                let (nm, en) = split_header(heap, buffer.bytes(rw), delim);
                                names = nm;
                                ends = en;
                            }
                            borrow ends as &er in {
                                s = Shape { columns: vec.size(er), rows: s.rows, ragged: s.ragged, first_row: s.first_row, first_line: s.first_line, first_found: s.first_found, truncated: s.truncated, abort: 0, abort_line: 0, long_length: 0 };
                            }
                            header = true;
                        } else {
                            var first_row = s.first_row;
                            var first_line = s.first_line;
                            var first_found = s.first_found;
                            var ragged = s.ragged;
                            if n + 1 != s.columns {
                                if ragged == 0 {
                                    first_row = s.rows + 1;
                                    first_line = opened;
                                    first_found = n + 1;
                                }
                                ragged = ragged + 1;
                            }
                            s = Shape { columns: s.columns, rows: s.rows + 1, ragged: ragged, first_row: first_row, first_line: first_line, first_found: first_found, truncated: false, abort: 0, abort_line: 0, long_length: 0 };
                        }
                    }
                }
            }
        }
    }
    if quoted && s.abort == 0 {
        s = Shape { columns: s.columns, rows: s.rows, ragged: s.ragged, first_row: s.first_row, first_line: s.first_line, first_found: s.first_found, truncated: s.truncated, abort: 2, abort_line: opened, long_length: 0 };
    }
    var errno = 0;
    borrow r as &rr in {
        errno = lines.failed(rr);
    }
    lines.drop(heap, r);
    buffer.drop(heap, raw);
    if errno != 0 {
        e = fail.io_error(heap, e, errno, false, shown);
    } else if s.abort == 4 {
        let over = limit.more(limit.none(), s.abort_line, s.long_length);
        e = limit.too_long(heap, e, args, parsed, table, shown, over, cap, line_ceiling(), "a line is longer than --max-line-bytes; the file was not read past it");
    } else if s.abort == 3 {
        var w = fail.open(heap, "limit.header-too-large", "the header record holds more than --max-line-bytes bytes", "raise --max-line-bytes, up to the ceiling introspect names");
        w = fail.repair_none(heap, w, "how large the header is was not read to the end");
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "path");
        w = text.put(heap, w, shown);
        w = json.put_key(heap, w, "line");
        w = json.put_int(heap, w, s.abort_line);
        w = json.put_key(heap, w, "limit");
        w = json.put_int(heap, w, cap);
        e = fail.add(heap, e, w);
    } else if s.abort == 2 {
        var w = fail.open(heap, "parse.csv-unterminated-quote", "a quoted field is open at the end of the input", "close the quote, or double the quotes that are text");
        w = fail.repair_none(heap, w, "which quote was meant to close is not known");
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "path");
        w = text.put(heap, w, shown);
        w = json.put_key(heap, w, "line");
        w = json.put_int(heap, w, s.abort_line);
        e = fail.add(heap, e, w);
    } else if s.abort == 1 {
        var w = fail.open(heap, "parse.csv-bad-quote", "a closing quote is followed by something other than the delimiter or the end of the record", "double a quote that is text, or put the delimiter after the closing quote");
        w = fail.repair_none(heap, w, "what the field was meant to hold is not known");
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "path");
        w = text.put(heap, w, shown);
        w = json.put_key(heap, w, "line");
        w = json.put_int(heap, w, s.abort_line);
        e = fail.add(heap, e, w);
    } else if s.ragged > 0 {
        var w = fail.open(heap, "parse.csv-ragged-row", "a row has a different number of fields than the header", "make every row as wide as the header, quoting fields that hold the delimiter");
        w = fail.repair_none(heap, w, "which fields a short or long row is missing or has too many of is not known");
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "path");
        w = text.put(heap, w, shown);
        w = json.put_key(heap, w, "ragged_rows");
        w = json.put_int(heap, w, s.ragged);
        w = json.put_key(heap, w, "first_row");
        w = json.put_int(heap, w, s.first_row);
        w = json.put_key(heap, w, "first_line");
        w = json.put_int(heap, w, s.first_line);
        w = json.put_key(heap, w, "expected");
        w = json.put_int(heap, w, s.columns);
        w = json.put_key(heap, w, "found");
        w = json.put_int(heap, w, s.first_found);
        e = fail.add(heap, e, w);
    }
    return (s, names, ends, e);
}

// The answer: `{"headers", "columns", "rows", "truncated"}` and its text form.
fn report[&h, &n, &d](heap: &!h Heap, s: Shape, names: &n buffer.Buffer, ends: &d vec.Vec[int]) -> [heap] (buffer.Buffer, buffer.Buffer) {
    var w = json.writer(heap, 256 + buffer.size(names) * 2);
    var plain = buffer.empty(heap, 256 + buffer.size(names));
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "headers");
    w = json.begin_array(heap, w);
    plain = buffer.append(heap, plain, "rows\t");
    plain = buffer.push_nat(heap, plain, s.rows);
    plain = buffer.append(heap, plain, "\ncolumns\t");
    plain = buffer.push_nat(heap, plain, s.columns);
    plain = buffer.append(heap, plain, "\ntruncated\t");
    if s.truncated {
        plain = buffer.append(heap, plain, "true");
    } else {
        plain = buffer.append(heap, plain, "false");
    }
    plain = buffer.append(heap, plain, "\nheader");
    var i = 0;
    var from = 0;
    while i < vec.size(ends) && s.columns > 0 {
        let to = vec.get(ends, i);
        let name = buffer.bytes(names)[from..to];
        w = text.put(heap, w, name);
        plain = buffer.push(heap, plain, byte_of(9));
        plain = buffer.append(heap, plain, name);
        from = to;
        i = i + 1;
    }
    plain = buffer.push(heap, plain, byte_of(10));
    w = json.end_array(heap, w);
    w = json.put_key(heap, w, "columns");
    w = json.put_int(heap, w, s.columns);
    w = json.put_key(heap, w, "rows");
    w = json.put_int(heap, w, s.rows);
    w = json.put_key(heap, w, "truncated");
    w = json.put_bool(heap, w, s.truncated);
    w = json.end_object(heap, w);
    return (json.finish(w), plain);
}

fn body[&h, &g, &p, &f, &i](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, errs: fail.Errors) -> [heap, args, fs_read(""), dir_read, file_read, io_write, err_write] int {
    let table = flag_table();
    var e = errs;
    let text_mode = bytes.equal(cli.text(args, parsed, table, "format"), "text");
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
    let (root, after_root) = path.root(heap, args, cli.text(args, parsed, table, "root"), cli.value_index(parsed, table, "root"), e);
    e = after_root;
    if cli.operand_count(parsed) == 0 {
        e = flag_problem(heap, e, "args.missing-operand", "table takes the FILE to describe", "table FILE", "FILE");
    } else if cli.operand_count(parsed) > 1 {
        e = flag_problem(heap, e, "args.too-many-operands", "table takes one FILE", "describe one file per call", "FILE");
    }
    var payload = buffer.empty(heap, 1);
    var plain = buffer.empty(heap, 1);
    var refused = false;
    borrow e as &er in {
        refused = fail.count(er) > 0;
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
                                let (s, names, ends, after) = read_file(heap, args, parsed, handle, path.shown(tp), delim, e);
                                e = after;
                                // What was read is the answer unless the read had to
                                // stop short of the end: a ragged row is an error
                                // about rows that were all counted.
                                if s.abort == 0 {
                                    borrow names as &nr in {
                                        borrow ends as &er in {
                                            buffer.drop(heap, payload);
                                            buffer.drop(heap, plain);
                                            let (d, t) = report(heap, s, nr, er);
                                            payload = d;
                                            plain = t;
                                        }
                                    }
                                }
                                buffer.drop(heap, names);
                                vec.drop(heap, ends);
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
    var status = 0;
    borrow e as &er in {
        borrow payload as &d in {
            borrow plain as &t in {
                status = out.respond(heap, io, "table", "table.v1", "0.1.0", buffer.bytes(d), "", er, text_mode, buffer.bytes(t), 0);
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
    let (parsed, e) = cli.parse(heap, args, flag_table(), fail.empty(heap));
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
