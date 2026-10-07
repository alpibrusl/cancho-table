edition 5;

// `table` -- a CSV or TSV file as a table: its shape, some of its columns, the
// rows that satisfy a condition, or counts and sums of groups of rows.
//
//     table [--root DIR] [--delimiter ,|tab|;] [--max-rows N] [--max-line-bytes N]
//           [--format json|text] FILE                                the shape
//     table [--select NAMES] [--where EXPR] [--order-by KEYS] [--top N] [--max-sort-rows N] [--limit N] [--from N] [--max-bytes N]
//           [--format json|csv] [the same] FILE                      rows
//     table --group NAMES [--agg LIST] [--where EXPR] [--sort KEY] [--top N]
//           [--limit N] [--from N] [--max-groups N] [--max-distinct N]
//           [--max-state-bytes N] [--format json|csv] [the same] FILE   groups
//
// Design and measurements: `docs/select.md`, `docs/filter.md`. The reader is
// `reader.ls` (RFC 4180, one line at a time), what is written for a field
// `writer.ls`, the plan `query.ls` (filled by `plan.ls` for column lists,
// `expr.ls` for `--where`, `agg.ls` for `--agg`), and what the header lets the
// plan become `frame.ls`; this file drives them and says what went wrong.
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
//   among the rows a selection returns or a grouping counts. A quote left open
//   at the end of the input is `parse.csv-unterminated-quote`.
// * One plan, one engine: the flags fill a `Query` (query.ls); a row is
//   counted, then tested by `--where`, then either emitted or added to its
//   group. `--where` is applied before grouping.
// * `--max-rows` is a bound on work, not memory: reading stops at it. In JSON
//   the answer says `truncated: true`; as CSV there is nowhere to say it, so it
//   is `limit.too-many-rows`. `--max-line-bytes` is a bound on memory: a
//   physical line longer than it is `limit.line-too-long`, and the header, and
//   with `--select`, `--where` or `--group` every record, which are kept while
//   they are used, may not hold more than that many bytes in all
//   (`limit.header-too-large`, `limit.record-too-large`).
//
// Memory is one 64 KiB chunk, the longest record, the header, one row of
// output and, as JSON, the page asked for (`--limit`, within `--max-bytes`);
// for a grouping, also its groups, bounded three ways (agg.ls).

import std.buffer;
import std.bytes;
import std.json;
import std.vec;
import agg;
import engine;
import expr;
import frame;
import par;
import plan;
import query;
import reader;
import scan;
import sorter;
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
    return "root||path|root||resolve FILE relative to this directory and refuse paths outside it;delimiter|d|text|none|,|the field separator: a comma, the word tab (or a tab character), or a semicolon;select||text|none||the columns to return, named by their header separated by commas (a comma or backslash in a name is written with a backslash before it), or by position as #3, and in the order given;where||text|none||only the rows that satisfy this: conditions joined by and, each COLUMN OP VALUE with OP one of = != < <= > >= contains in (a, b), COLUMN:int for an exact integer comparison or COLUMN:dec(N) for an exact decimal one of at most N fractional digits, 'quoted' words and backslashes as docs/filter.md says;group||text|none||count the rows by these columns, named as for select, instead of returning them;agg||text|none||with --group (or alone), what to compute per group: count, sum:COL, min:COL, max:COL, distinct:COL separated by commas (default count);sort||text|none||with --group, order the groups by this output column (count, sum:bytes, a group column), - before it for descending, ties by group;top||nat|none||with --group or --order-by, keep only the first N groups or rows after sorting;limit||nat|none||the most rows returned (default 1000 as json, no limit as csv, ceiling 1000000);from||nat|none||the 0-based row to start at, as the next of a truncated answer says;max-bytes||nat|none|1048576|the most json rows returned hold, past it the answer is truncated with a next (ceiling 67108864);max-groups||nat|none|100000|the most groups kept, one more is limit.too-many-groups (ceiling 1000000);max-distinct||nat|none|100000|the most distinct values kept over all groups for distinct, one more is limit.too-many-distinct (ceiling 10000000);max-state-bytes||nat|none|67108864|the most bytes of group keys, distinct values or sorted rows kept (ceiling 1073741824);order-by||text|none||sort the rows by these keys, each NAME, -NAME for descending, NAME:int for exact integers, separated by commas, ties keep the order of the file (docs/sort.md);max-sort-rows||nat|none|1000000|the most rows held to sort, one more is limit.too-many-sort-rows (ceiling 20000000);threads||nat|none|1|how many threads read the file (1 to 64), for rows and groups only, on a file of at least --parallel-min-bytes of data: 1 is the sequential read and the answer of every other count is the same bytes;chunk-bytes||nat|none|4194304|the size of the range one thread reads at a time (with --threads), at least 1;parallel-min-bytes||nat|none|1048576|the smallest data (after the header) read by several threads, below it the read is sequential;max-rows||nat|none|10000000|the most data rows read, past it the answer says truncated (ceiling 1000000000);max-line-bytes||nat|none|1048576|the longest line read and the most a kept record may hold, a longer one is limit.line-too-long (ceiling 16777216);format||choice:json/text/csv|none|json|json for a program, text for a person (the shape only), csv for rows or groups as csv";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "table", version: "0.3.0", summary: "A CSV or TSV file as a table, read in one bounded streaming pass over an RFC 4180 reader: its shape, some columns (--select), the rows that satisfy a condition (--where, bytewise or exact integers or decimals), or counts, sums, minima, maxima and distinct counts of groups (--group, --agg, --sort, --top), as json pages or csv. Every refusal is a rule: a ragged row, an unterminated quote, an unknown column, a cell that is not an integer, a line past the limit.", usage: "table [--root DIR] [--delimiter ,|tab|;] [--max-rows N] [--max-line-bytes N] [--format json|text] FILE | table [--select NAMES] [--where EXPR] [--limit N] [--from N] [--max-bytes N] [--format json|csv] FILE | table --group NAMES [--agg LIST] [--where EXPR] [--sort KEY] [--top N] [--max-groups N] [--max-distinct N] [--max-state-bytes N] [--format json|csv] FILE", output: "document", schema: "table.v2", flags: flag_table(), operands: "FILE|path-read|1|1|the CSV file to read", rules: "args.unknown-flag;args.missing-value;args.bad-value;args.duplicate-flag;args.conflict;args.required-flag;args.missing-operand;args.too-many-operands;path.empty;path.dotdot;path.absolute;path.outside-root;path.too-long;path.symlink;io.not-found;io.not-a-directory;io.is-a-directory;io.permission-denied;io.read-failed;limit.line-too-long;limit.header-too-large;limit.record-too-large;limit.output-too-large;limit.too-many-rows;limit.too-many-sort-rows;limit.too-many-groups;limit.too-many-distinct;limit.state-too-large;parse.csv-ragged-row;parse.csv-bad-quote;parse.csv-unterminated-quote;select.unknown-column;select.ambiguous-column;column.unknown;column.ambiguous;where.syntax;agg.bad-spec;sort.unknown-key;value.not-integer;value.integer-overflow;value.not-decimal;value.decimal-scale;value.decimal-too-wide;column.type-conflict", extra_rules: extra(), limits: "threads|1|64;chunk-bytes|4194304|1073741824;parallel-min-bytes|1048576|1073741824;limit|1000|1000000;max-bytes|1048576|67108864;max-groups|100000|1000000;max-distinct|100000|10000000;max-state-bytes|67108864|1073741824;max-sort-rows|1000000|20000000;max-rows|10000000|1000000000;max-line-bytes|1048576|16777216", reversibility: "reversible-cheap", stdin: "no", guarantees: "deterministic;idempotent;bounded_memory" };
}

// The tool's own rules, beside the contract's catalogue: tag, exit code,
// repairable, summary.
fn extra() -> [] &static [byte] {
    return "limit.header-too-large|8|never|the header record holds more than --max-line-bytes bytes;limit.record-too-large|8|never|a record read for --select, --where, --order-by or --group holds more than --max-line-bytes bytes;limit.output-too-large|8|never|the first row of a page is longer than --max-bytes;limit.too-many-rows|8|never|--format csv reached --max-rows with rows left unread;limit.too-many-sort-rows|8|never|--order-by would hold more than --max-sort-rows rows to sort;limit.too-many-groups|8|never|more groups than --max-groups;limit.too-many-distinct|8|never|more distinct values than --max-distinct;limit.state-too-large|8|never|the keys and values kept for groups, or the rows kept to sort, hold more than --max-state-bytes bytes;parse.csv-ragged-row|8|never|a row has a different number of fields than the header;parse.csv-bad-quote|8|never|a closing quote is followed by something other than the delimiter or the end of the record;parse.csv-unterminated-quote|8|never|a quoted field is still open at the end of the input;select.unknown-column|3|sometimes|a name or position in --select that is not a column of the header;select.ambiguous-column|8|never|a name in --select that is the name of more than one column;column.unknown|3|never|a name or position in --where, --group or --agg that is not a column of the header;column.ambiguous|8|never|a name in --where, --group or --agg that is the name of more than one column;where.syntax|2|never|--where is not an expression of the grammar, at the offset the detail gives;agg.bad-spec|2|never|an item of --agg that is not count, sum:COL, min:COL, max:COL, mean:COL or distinct:COL, or whose :int, :dec(S) or @N is not one it takes;sort.unknown-key|2|never|--sort names no output column of the grouping;value.not-integer|8|never|a cell of an :int column or of sum, min or max is not an exact integer (an empty cell is not);value.integer-overflow|8|never|a cell of an :int column or of sum, min or max does not fit 64 bits;value.not-decimal|8|never|a cell of a :dec(S) column is not a decimal: an empty cell, an exponent, a space, a separator, a sign or a point alone;value.decimal-scale|8|sometimes|a cell of a :dec(S) column has more fractional digits than S (it is never rounded);value.decimal-too-wide|8|never|a cell of a :dec(S) column has 18 or more significant digits once scaled to S;column.type-conflict|2|never|the plan reads one column as two numeric types: :int, or :dec with two scales";
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
    w = fail.detail_str(heap, w, "flags", flags);
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
// its limit, 8 a first row past the budget, 9 standard output refused a write,
// 11 and 12 --where met a cell that is not an integer / too large, 13 more
// groups than allowed, 14 more distinct values, 15 more key bytes, 16 and 17 an
// aggregate met a cell that is not an integer / too large, 26, 27 and 28 one that is not a decimal / has too many fractional digits / is too wide, (18 was a sum past 64
// bits: a sum is a pair of integers now and cannot leave them), 19 a --sort key that is no output column, 22, 23 and 24 --where met a
// :dec cell that is not a decimal / has more fractional digits than the scale / is too wide, 25 a column read as two numeric types; `abort_line` is the line it is
// about.
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
    err_col: int,
    err_fn: int,
    err_row: int,
    err_line: int,
    err_cond: int,
    err_digits: int,
    groups: int,
}

// The groups, in order, as the answer: the page `--from` and `--limit` ask for
// of the first `--top` of them, as csv written out or as json rows kept.
fn finish_groups[&h, &i, &g, &q, &a](heap: &!h Heap, io: &!i Io, rows: buffer.Buffer, scratch: buffer.Buffer, groups: &g agg.Groups, tree: &q query.Query, as_csv: bool, delim: int, from: int, limit: int, top: int, budget: int, a: &!a [int]) -> [heap, io_write] (buffer.Buffer, buffer.Buffer) {
    let n = agg.groups(groups);
    a[engine.k_groups()] = n;
    let ng = query.count_of(tree, 2);
    let order = box_slice(heap, n + 1, 0);
    let spare = box_slice(heap, n + 1, 0);
    let prefixes = box_slice(heap, n + 1, 0);
    var pending = rows;
    var row = scratch;
    borrow mut order as &!ow in {
        borrow mut spare as &!sw in {
            borrow mut prefixes as &!pw in {
                agg.sort_into(groups, contents(ow), contents(sw), contents(pw), ng, a[engine.k_sort_field()], a[engine.k_sort_slot()], a[engine.k_sort_desc()] == 1);
                var end = n;
                if top > 0 && top < n {
                    end = top;
                }
                var pos = from;
                var going = true;
                while pos < end && going {
                    let x = contents(ow)[pos];
                    if a[engine.k_emitted()] >= limit {
                        a[engine.k_more()] = 1;
                        a[engine.k_next()] = pos;
                        going = false;
                    } else if as_csv {
                        var j = 0;
                        while j < ng {
                            if j > 0 {
                                pending = buffer.push(heap, pending, byte_of(delim));
                            }
                            pending = writer.csv_value(heap, pending, agg.key_field(groups, x, j), delim);
                            j = j + 1;
                        }
                        var k = 0;
                        while k < query.agg_count(tree) {
                            if ng + k > 0 {
                                pending = buffer.push(heap, pending, byte_of(delim));
                            }
                            pending = agg.put_value(heap, pending, groups, tree, x, k);
                            k = k + 1;
                        }
                        pending = buffer.push(heap, pending, byte_of(10));
                        a[engine.k_emitted()] = a[engine.k_emitted()] + 1;
                        var held = 0;
                        borrow pending as &pr in {
                            held = buffer.size(pr);
                        }
                        if held >= 65536 {
                            let (after, ok) = engine.flush(io, pending);
                            pending = after;
                            if !ok {
                                a[engine.k_abort()] = 9;
                                going = false;
                            }
                        }
                        pos = pos + 1;
                    } else {
                        borrow mut row as &!rw in {
                            buffer.clear(rw);
                        }
                        row = agg.row_json(heap, row, groups, tree, x);
                        var have = 0;
                        var need = 0;
                        borrow pending as &pr in {
                            have = buffer.size(pr);
                        }
                        borrow row as &rr in {
                            need = buffer.size(rr);
                        }
                        if have + need + 1 > budget {
                            going = false;
                            if a[engine.k_emitted()] == 0 {
                                a[engine.k_abort()] = 8;
                            } else {
                                a[engine.k_more()] = 1;
                                a[engine.k_next()] = pos;
                            }
                        } else {
                            if a[engine.k_emitted()] > 0 {
                                pending = buffer.push(heap, pending, byte_of(','));
                            }
                            borrow row as &rr in {
                                pending = buffer.append(heap, pending, buffer.bytes(rr));
                            }
                            a[engine.k_emitted()] = a[engine.k_emitted()] + 1;
                            pos = pos + 1;
                        }
                    }
                }
            }
        }
    }
    unbox_slice(heap, order);
    unbox_slice(heap, spare);
    unbox_slice(heap, prefixes);
    return (pending, row);
}

// The header's own spelling of column `col`.
fn name_of[&n, &e](names: &n buffer.Buffer, ends: &e vec.Vec[int], col: int) -> [] &n [byte] {
    var begin = 0;
    if col > 0 {
        begin = vec.get(ends, col - 1);
    }
    return buffer.bytes(names)[begin..vec.get(ends, col)];
}

// A refusal at one line of the file: the rule's detail is the path and the line.
fn at_line[&h, &s](heap: &!h Heap, e: fail.Errors, rule: &static [byte], message: &static [byte], hint: &static [byte], reason: &static [byte], shown: &s [byte], line: int) -> [heap] fail.Errors {
    var w = fail.open_in(heap, extra(), rule, message, hint);
    w = fail.repair_none(heap, w, reason);
    w = fail.detail_open(heap, w);
    w = fail.detail_text(heap, w, "path", shown);
    w = fail.detail_int(heap, w, "line", line);
    return fail.add(heap, e, w);
}

// A refusal of a limit: the rule's detail is the path and the limit.
fn over_limit[&h, &s](heap: &!h Heap, e: fail.Errors, rule: &static [byte], message: &static [byte], hint: &static [byte], shown: &s [byte], bound: int) -> [heap] fail.Errors {
    var w = fail.open_in(heap, extra(), rule, message, hint);
    w = fail.repair_none(heap, w, "how large the whole is was not measured past the limit");
    w = fail.detail_open(heap, w);
    w = fail.detail_text(heap, w, "path", shown);
    w = fail.detail_int(heap, w, "limit", bound);
    return fail.add(heap, e, w);
}

// What a refusal says it was reading for: the function of the aggregate, or the condition (`err_fn` -1), or the sort key (-2).
fn context_of(function: int) -> [] &static [byte] {
    if function == -2 {
        return "order-by";
    }
    if function == 1 {
        return "sum";
    }
    if function == 2 {
        return "min";
    }
    if function == 3 {
        return "max";
    }
    if function == 4 {
        return "distinct";
    }
    if function == 5 {
        return "mean";
    }
    return "where";
}

// A type code (`query.ls`: 1 :int, 2 + S :dec(S)) as it is written.
fn type_name(t: int) -> [] &static [byte] {
    if t == 1 {
        return ":int";
    }
    if t == 2 {
        return ":dec(0)";
    }
    if t == 3 {
        return ":dec(1)";
    }
    if t == 4 {
        return ":dec(2)";
    }
    if t == 5 {
        return ":dec(3)";
    }
    if t == 6 {
        return ":dec(4)";
    }
    if t == 7 {
        return ":dec(5)";
    }
    if t == 8 {
        return ":dec(6)";
    }
    if t == 9 {
        return ":dec(7)";
    }
    if t == 10 {
        return ":dec(8)";
    }
    if t == 11 {
        return ":dec(9)";
    }
    if t == 12 {
        return ":dec(10)";
    }
    if t == 13 {
        return ":dec(11)";
    }
    if t == 14 {
        return ":dec(12)";
    }
    if t == 15 {
        return ":dec(13)";
    }
    if t == 16 {
        return ":dec(14)";
    }
    if t == 17 {
        return ":dec(15)";
    }
    if t == 18 {
        return ":dec(16)";
    }
    if t == 19 {
        return ":dec(17)";
    }
    return ":dec(18)";
}

// `--where` with the first `:dec(S)` at or after `offset` written `:dec(scale)`: the repair of a cell with more fractional digits than
// the scale. (`offset` is where the condition begins, and its column is the first thing in it.)
fn rewrite_scale[&h, &g](heap: &!h Heap, given: &g [byte], offset: int, scale: int) -> [heap] buffer.Buffer {
    var out = buffer.empty(heap, len(given) + 4);
    let found = bytes.find(given[offset..len(given)], ":dec(");
    if found < 0 {
        return buffer.append(heap, out, given);
    }
    let at = offset + found + 5;
    var end = at;
    while end < len(given) && int_of(given[end]) >= '0' && int_of(given[end]) <= '9' {
        end = end + 1;
    }
    out = buffer.append(heap, out, given[0..at]);
    out = buffer.push_nat(heap, out, scale);
    return buffer.append(heap, out, given[end..len(given)]);
}

// What went wrong in a read, as an error with a rule, or nothing.
fn explain[&h, &g, &p, &q, &n, &d, &l, &m, &k, &s](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, tree: &q query.Query, names: &n buffer.Buffer, hends: &d vec.Vec[int], labels: &l buffer.Buffer, lends: &m vec.Vec[int], kept: &k buffer.Buffer, c: Counts, shown: &s [byte], names_at: int, errno: int, as_csv: bool, budget: int, errs: fail.Errors) -> [heap, args] fail.Errors {
    let table = flag_table();
    let cap = cli.nat(args, parsed, table, "max-line-bytes");
    let most = cli.nat(args, parsed, table, "max-rows");
    var e = errs;
    if errno != 0 {
        e = fail.io_error(heap, e, errno, false, shown);
    } else if c.abort == 4 {
        let over = limit.more(limit.none(), c.abort_line, c.long_length);
        e = limit.too_long(heap, e, args, parsed, table, shown, over, cap, line_ceiling(), "a line is longer than --max-line-bytes; the file was not read past it");
    } else if c.abort == 3 {
        e = at_line(heap, e, "limit.header-too-large", "the header record holds more than --max-line-bytes bytes", "raise --max-line-bytes, up to the ceiling introspect names", "how large the record is was not read to the end", shown, c.abort_line);
    } else if c.abort == 7 {
        e = at_line(heap, e, "limit.record-too-large", "a record holds more than --max-line-bytes bytes", "raise --max-line-bytes, up to the ceiling introspect names", "how large the record is was not read to the end", shown, c.abort_line);
    } else if c.abort == 2 {
        e = at_line(heap, e, "parse.csv-unterminated-quote", "a quoted field is open at the end of the input", "close the quote, or double the quotes that are text", "which quote was meant to close is not known", shown, c.abort_line);
    } else if c.abort == 1 {
        e = at_line(heap, e, "parse.csv-bad-quote", "a closing quote is followed by something other than the delimiter or the end of the record", "double a quote that is text, or put the delimiter after the closing quote", "what the field was meant to hold is not known", shown, c.abort_line);
    } else if c.abort == 5 || c.abort == 6 {
        let ns = query.count_of(tree, 0);
        let nw = query.count_of(tree, 1);
        let ng = query.count_of(tree, 2);
        var flag = "--select";
        var rule = "select.unknown-column";
        var what = "--select names a column the header does not have";
        var hint = "pick from detail.available";
        var upto = ns;
        var at_arg = names_at;
        if c.bad_name >= ns {
            flag = "--where";
            if c.bad_name >= query.name_count(tree) - query.count_of(tree, 4) && query.count_of(tree, 4) > 0 {
                flag = "--order-by";
            } else if c.bad_name >= ns + nw + ng {
                flag = "--agg";
            } else if c.bad_name >= ns + nw {
                flag = "--group";
            }
            rule = "column.unknown";
            what = "the plan names a column the header does not have";
            at_arg = -1;
        }
        if c.abort == 6 {
            rule = "select.ambiguous-column";
            what = "--select names a column that the header has more than once";
            if c.bad_name >= ns {
                rule = "column.ambiguous";
                what = "the plan names a column that the header has more than once";
            }
            hint = "name it by position, as #3 for the third column";
        }
        e = plan.refusal(heap, e, extra(), rule, what, hint, args, at_arg, names, hends, tree.names, tree.nends, tree.nkinds, c.bad_name, upto, flag, shown);
    } else if c.abort == 8 {
        e = over_limit(heap, e, "limit.output-too-large", "the first row of the page is longer than --max-bytes", "raise --max-bytes, up to the ceiling introspect names, or select fewer columns", shown, budget);
    } else if c.abort == 20 {
        e = over_limit(heap, e, "limit.too-many-sort-rows", "--order-by would hold more than --max-sort-rows rows to sort", "ask for the first rows only with --top N or a smaller --limit (they are kept in bounded memory), keep fewer rows with --where, or raise --max-sort-rows, up to the ceiling introspect names", shown, cli.nat(args, parsed, table, "max-sort-rows"));
    } else if c.abort == 21 {
        e = over_limit(heap, e, "limit.state-too-large", "the rows kept to sort hold more than --max-state-bytes bytes", "ask for the first rows only with --top N or a smaller --limit, keep fewer or narrower rows with --where, or raise --max-state-bytes, up to the ceiling introspect names", shown, cli.nat(args, parsed, table, "max-state-bytes"));
    } else if c.abort == 13 {
        e = over_limit(heap, e, "limit.too-many-groups", "more groups than --max-groups; counting stopped there", "raise --max-groups, up to the ceiling introspect names, or group by fewer or coarser columns", shown, cli.nat(args, parsed, table, "max-groups"));
    } else if c.abort == 14 {
        e = over_limit(heap, e, "limit.too-many-distinct", "more distinct values than --max-distinct; counting stopped there", "raise --max-distinct, up to the ceiling introspect names", shown, cli.nat(args, parsed, table, "max-distinct"));
    } else if c.abort == 15 {
        e = over_limit(heap, e, "limit.state-too-large", "the group keys and distinct values kept hold more than --max-state-bytes bytes", "raise --max-state-bytes, up to the ceiling introspect names, or group by shorter values", shown, cli.nat(args, parsed, table, "max-state-bytes"));
    } else if c.abort == 11 || c.abort == 12 || c.abort == 16 || c.abort == 17 {
        var rule = "value.not-integer";
        var what = "a cell is not an exact integer: an optional sign and digits, nothing else (an empty cell is not one)";
        var hint = "keep out the rows with such a cell with --where, or do not ask for an integer of this column (a cell with a point is a decimal: declare the column :dec(N))";
        if c.abort == 12 || c.abort == 17 {
            rule = "value.integer-overflow";
            what = "a cell is an integer that does not fit 64 bits";
        }
        let context = context_of(c.err_fn);
        var w = fail.open_in(heap, extra(), rule, what, hint);
        w = fail.repair_none(heap, w, "what the cell was meant to be is not known");
        w = fail.detail_open(heap, w);
        w = fail.detail_text(heap, w, "path", shown);
        w = fail.detail_str(heap, w, "context", context);
        w = fail.detail_text(heap, w, "column", name_of(names, hends, c.err_col));
        w = fail.detail_int(heap, w, "row", c.err_row);
        w = fail.detail_int(heap, w, "line", c.err_line);
        w = fail.detail_text(heap, w, "value", buffer.bytes(kept));
        w = fail.detail_bool(heap, w, "value_truncated", buffer.size(kept) >= 64);
        e = fail.add(heap, e, w);
    } else if c.abort == 22 || c.abort == 23 || c.abort == 24 || c.abort == 26 || c.abort == 27 || c.abort == 28 {
        // a :dec cell refused by --where (22 to 24) or by an aggregate (26 to 28, the same three in the same order)
        let in_agg = c.abort >= 26;
        var code = c.abort;
        var scale = 0;
        if in_agg {
            code = c.abort - 4;
            scale = query.agg_at(tree, c.err_cond, 2) - 2;
        } else {
            scale = query.cond_at(tree, c.err_cond, 1) - 2;
        }
        var rule = "value.not-decimal";
        var what = "a cell of a :dec column is not a decimal: digits and at most one point, optionally signed, nothing else (an empty cell is not one)";
        var hint = "keep out the rows with such a cell with --where first (a condition that is false stops the ones after it), or do not ask for a decimal of this column";
        if code == 23 {
            rule = "value.decimal-scale";
            what = "a cell of a :dec column has more fractional digits than the scale; a decimal is never rounded";
            hint = "declare a larger scale, :dec(N) with N the most fractional digits the column holds";
        } else if code == 24 {
            rule = "value.decimal-too-wide";
            what = "a cell of a :dec column has 18 or more significant digits once scaled to the column's scale";
            hint = "declare a smaller scale, or compare the column as text";
        }
        let digits = c.err_digits;
        var w = fail.open_in(heap, extra(), rule, what, hint);
        let given = cli.text(args, parsed, table, "where");
        let where_at = cli.value_index(parsed, table, "where");
        if code == 23 && where_at >= 0 && !in_agg {
            // the whole invocation with this condition's :dec(S) written :dec(digits)
            let fixed = rewrite_scale(heap, given, query.cond_at(tree, c.err_cond, 6), digits);
            var o = fail.choose_open(heap, w);
            borrow fixed as &fr in {
                o = fail.choose_option_replacing(heap, o, args, where_at, buffer.bytes(fr));
            }
            buffer.drop(heap, fixed);
            w = fail.choose_close(heap, o);
        } else if code == 22 {
            w = fail.repair_none(heap, w, "what the cell was meant to be is not known (an exponent is a float, which this tool does not read yet)");
        } else if code == 23 {
            w = fail.repair_none(heap, w, "write :dec(N) in the aggregate with N the digits of the cell, or more");
        } else {
            w = fail.repair_none(heap, w, "what the cell was meant to be is not known");
        }
        w = fail.detail_open(heap, w);
        w = fail.detail_text(heap, w, "path", shown);
        w = fail.detail_str(heap, w, "context", context_of(c.err_fn));
        w = fail.detail_text(heap, w, "column", name_of(names, hends, c.err_col));
        w = fail.detail_int(heap, w, "row", c.err_row);
        w = fail.detail_int(heap, w, "line", c.err_line);
        w = fail.detail_text(heap, w, "value", buffer.bytes(kept));
        w = fail.detail_bool(heap, w, "value_truncated", buffer.size(kept) >= 64);
        w = fail.detail_int(heap, w, "scale", scale);
        if code == 23 {
            w = fail.detail_int(heap, w, "digits", digits);
        }
        e = fail.add(heap, e, w);
    } else if c.abort == 25 {
        let column = c.bad_name / 1024;
        var w = fail.open_in(heap, extra(), "column.type-conflict", "the plan reads one column as two numeric types", "give the column one type: the same :int, or the same :dec(N), in every place it is named");
        w = fail.repair_none(heap, w, "which type was meant is not known");
        w = fail.detail_open(heap, w);
        w = fail.detail_text(heap, w, "column", name_of(names, hends, column));
        w = json.put_key(heap, w, "types");
        w = json.begin_array(heap, w);
        w = json.put_string(heap, w, type_name(c.bad_name / 32 % 32));
        w = json.put_string(heap, w, type_name(c.bad_name % 32));
        w = json.end_array(heap, w);
        e = fail.add(heap, e, w);
    } else if c.abort == 19 {
        var w = fail.open_in(heap, extra(), "sort.unknown-key", "--sort names no output column of the grouping", "pick from detail.available, with a - before it to sort descending");
        w = fail.repair_none(heap, w, "which column was meant is not known");
        w = fail.detail_open(heap, w);
        w = fail.detail_str(heap, w, "sort", cli.text(args, parsed, table, "sort"));
        w = json.put_key(heap, w, "available");
        w = json.begin_array(heap, w);
        var i = 0;
        var begin = 0;
        while i < vec.size(lends) {
            let to = vec.get(lends, i);
            w = text.put(heap, w, buffer.bytes(labels)[begin..to]);
            begin = to;
            i = i + 1;
        }
        w = json.end_array(heap, w);
        e = fail.add(heap, e, w);
    } else if (as_csv || query.count_of(tree, 4) > 0) && c.capped {
        e = over_limit(heap, e, "limit.too-many-rows", "--max-rows data rows were read and there are more", "raise --max-rows, up to the ceiling introspect names, or page with --limit and --from", shown, most);
    } else if c.ragged > 0 {
        var w = fail.open_in(heap, extra(), "parse.csv-ragged-row", "a row has a different number of fields than the header", "make every row as wide as the header, quoting fields that hold the delimiter");
        w = fail.repair_none(heap, w, "which fields a short or long row is missing or has too many of is not known");
        w = fail.detail_open(heap, w);
        w = fail.detail_text(heap, w, "path", shown);
        w = fail.detail_int(heap, w, "ragged_rows", c.ragged);
        w = fail.detail_int(heap, w, "first_row", c.first_row);
        w = fail.detail_int(heap, w, "first_line", c.first_line);
        w = fail.detail_int(heap, w, "expected", c.columns);
        w = fail.detail_int(heap, w, "found", c.first_found);
        e = fail.add(heap, e, w);
    }
    return e;
}

// The output of a csv answer starts with its header line.
fn start_output[&h](heap: &!h Heap, rows: buffer.Buffer, lead: buffer.Buffer) -> [heap] buffer.Buffer {
    var r = rows;
    var empty = true;
    borrow lead as &lr in {
        empty = buffer.size(lr) == 0;
        r = buffer.append(heap, r, buffer.bytes(lr));
    }
    if empty {
        // One empty name is `""`: a blank line is no record.
        r = buffer.append(heap, r, "\"\"");
    }
    r = buffer.push(heap, r, byte_of(10));
    buffer.drop(heap, lead);
    return r;
}

// The sorted rows of `--order-by`, written as `--select` writes them: the rows from `from` on, up to `limit` and `top`, as
// csv written out or as json kept (a page that stops short because of the byte budget or the limit says where the next one
// begins: `next` is a place in the sorted answer).
fn finish_sort[&h, &i, &s, &l, &c, &a](heap: &!h Heap, io: &!i Io, rows: buffer.Buffer, scratch: buffer.Buffer, held: &s agg.Groups, sel: &l [int], cells: &!c [int], picked: int, as_csv: bool, delim: int, from: int, limit: int, top: int, budget: int, a: &!a [int]) -> [heap, io_write] (buffer.Buffer, buffer.Buffer) {
    let n = sorter.held(held);
    var end = n;
    if top > 0 && top < n {
        end = top;
    }
    var pending = rows;
    var row = scratch;
    let ord = sorter.order(heap, held);
    var pos = from;
    var going = true;
    while pos < end && going {
        if a[engine.k_emitted()] >= limit {
            a[engine.k_more()] = 1;
            a[engine.k_next()] = pos;
            going = false;
        } else {
            borrow ord as &or in {
                let record = sorter.record_of(held, contents(or)[pos]);
                let (found, open, bad) = reader.fields(record, delim, cells, a[engine.k_columns()]);
                let (p2, r2) = engine.emit_row(heap, io, pending, row, record, cells, sel, picked, delim, as_csv, budget, a);
                pending = p2;
                row = r2;
            }
            if a[engine.k_stop()] == 1 {
                going = false;
                if a[engine.k_more()] == 1 {
                    a[engine.k_next()] = pos;
                }
            } else {
                pos = pos + 1;
            }
        }
    }
    unbox_slice(heap, ord);
    return (pending, row);
}

// Read the open file. Mode 0 counts (the shape); mode 1 writes the rows the plan
// selects and keeps, mode 2 counts them into groups and writes the groups: as
// csv straight to standard output, as json into the buffer answered third (the
// second is the answer's `columns` array text). Answers what was counted, those
// two buffers, the header's names and where each ends, and the errors.
fn read_file[&h, &g, &p, &f, &s, &i, &q, &fs, &rt, &rl, &fu](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, file: &!f File, shown: &s [byte], io: &!i Io, fs: &fs Fs(""), root: &rt [byte], rel: &rl [byte], full: &fu [byte], delim: int, mode: int, as_csv: bool, from: int, limit: int, budget: int, top: int, tree: &q query.Query, names_at: int, errs: fail.Errors) -> [heap, args, file_read, io_write, conc, fs_read(""), dir_read] (Counts, buffer.Buffer, buffer.Buffer, buffer.Buffer, vec.Vec[int], fail.Errors) {
    let table = flag_table();
    let cap = cli.nat(args, parsed, table, "max-line-bytes");
    let most = cli.nat(args, parsed, table, "max-rows");
    var max_groups = cli.nat(args, parsed, table, "max-groups");
    let max_distinct = cli.nat(args, parsed, table, "max-distinct");
    let max_state = cli.nat(args, parsed, table, "max-state-bytes");
    let threads = cli.nat(args, parsed, table, "threads");
    let chunk = cli.nat(args, parsed, table, "chunk-bytes");
    let min_bytes = cli.nat(args, parsed, table, "parallel-min-bytes");
    var sort = "";
    if cli.has(parsed, table, "sort") {
        sort = cli.text(args, parsed, table, "sort");
    }
    let filtering = query.count_of(tree, 1) > 0;
    let fast_ok = engine.fast_ok(tree);
    let ordering = query.count_of(tree, 4) > 0;
    let max_sort_rows = cli.nat(args, parsed, table, "max-sort-rows");
    // `--order-by` goes where the grouping goes: the rows are held in the groups' place (sorter.ls), and the bound on the groups is
    // the bound on the rows. The loop below is not told: a third thing to do with a row, there, made it 15 percent slower for the other two.
    var row_mode = mode;
    if ordering {
        row_mode = 2;
        max_groups = max_sort_rows;
    }
    // The rows of the answer that are wanted, when they are not all of them: a page (the one past it says there is
    // more) or `--top`. 0 is all of them, and also when twice that and one is past `--max-sort-rows`.
    var wanted = 0;
    if ordering {
        if limit < 100000000 {
            wanted = from + limit + 1;
        }
        if top > 0 && (wanted == 0 || top < wanted) {
            wanted = top;
        }
        if wanted > 0 && 2 * wanted + 1 > max_sort_rows {
            wanted = 0;
        }
    }
    // `from` pages the rows of a selection; for a grouping it pages the groups, after all
    // the rows have been counted, and must not skip any.
    var row_from = from;
    if mode == 2 || ordering {
        row_from = 0;
    }
    var e = errs;
    var names = buffer.empty(heap, 1);
    var hends = vec.empty(heap, 1, 0);
    var head = buffer.empty(heap, 1);
    var rows = buffer.empty(heap, 1);
    var scratch = buffer.empty(heap, 1);
    var escr = buffer.empty(heap, 16);
    var kept = buffer.empty(heap, 64);
    var groups = agg.start(heap, query.agg_count(tree));
    var rec = buffer.empty(heap, 256);
    var cols = box_slice(heap, 1, 0);
    var cells = box_slice(heap, 3, 0);
    var sel = box_slice(heap, 1, 0);
    var labels = buffer.empty(heap, 1);
    var lends = vec.empty(heap, 1, 0);
    var tally = box_slice(heap, engine.k_size(), 0);
    var out_rows = buffer.empty(heap, 1);
    var out_kept = buffer.empty(heap, 1);
    var picked = 0;
    var header = false;
    var quoted = false;
    var seps = 0;
    var opened = 0;
    var r = lines.start(heap, cap);
    var going = true;
    var c = Counts { columns: 0, records: 0, ragged: 0, first_row: 0, first_line: 0, first_found: 0, capped: false, abort: 0, abort_line: 0, long_length: 0, emitted: 0, more: false, next: 0, bad_name: 0, err_col: 0, err_fn: 0, err_row: 0, err_line: 0, err_cond: 0, err_digits: 0, groups: 0 };
    borrow mut tally as &!tw in {
        let a = contents(tw);
        while going {
            var status = 9;
            borrow mut r as &!rw in {
                status = scan.next_fast(rw);
            }
            if status == 9 {
                let (stepped, answer) = lines.next(heap, r);
                r = stepped;
                status = answer;
            }
            if status == lines.need() {
                r = lines.fill_file(r, file);
            } else if status == lines.done() {
                going = false;
            } else if status == lines.long() {
                a[engine.k_abort()] = 4;
                borrow r as &rr in {
                    a[engine.k_abort_line()] = lines.number(rr);
                    a[engine.k_long()] = lines.length(rr);
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
                    } else if header && !quoted && a[engine.k_records()] >= most {
                        // A record past the bound: not read, so not judged.
                        a[engine.k_capped()] = 1;
                        going = false;
                    } else if header && !quoted && mode == 1 && !filtering && a[engine.k_records()] >= from && a[engine.k_emitted()] >= limit {
                        // The page is full and there is another record.
                        a[engine.k_more()] = 1;
                        a[engine.k_next()] = a[engine.k_records()];
                        going = false;
                    } else if header && !quoted {
                        // A data record begins; most are whole on this line.
                        var inside = false;
                        var found = 0;
                        var bad = false;
                        if mode != 0 {
                            borrow mut cells as &!cw in {
                                let (n, open, wrong) = reader.fields(line, delim, contents(cw), a[engine.k_columns()]);
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
                            a[engine.k_abort()] = 1;
                            a[engine.k_abort_line()] = number;
                            going = false;
                        } else if inside {
                            quoted = true;
                            opened = number;
                            borrow mut rec as &!rw in {
                                buffer.clear(rw);
                            }
                            if mode != 0 {
                                rec = buffer.append(heap, rec, whole);
                                rec = buffer.push(heap, rec, byte_of(10));
                            }
                        } else if engine.count_row(a, found, number, row_from) && mode != 0 {
                            borrow cells as &cr in {
                                borrow sel as &sr in {
                                    borrow cols as &kr in {
                                        if row_mode == 1 {
                                            let (r2, s2, e2, k2) = engine.process_rows(heap, io, rows, scratch, escr, kept, tree, contents(kr), line, contents(cr), contents(sr), picked, delim, as_csv, budget, number, limit, a);
                                            rows = r2;
                                            scratch = s2;
                                            escr = e2;
                                            kept = k2;
                                        } else {
                                            var hot = 1;
                                            if !fast_ok {
                                                // a distinct count is not added in place: `process_groups`
                                            } else if !filtering {
                                                var got = 1;
                                                borrow mut groups as &!gw in {
                                                    got = engine.group_plain(gw, tree, contents(kr), line, contents(cr));
                                                }
                                                if got >= 16 {
                                                    kept = engine.group_refused(heap, kept, tree, contents(kr), line, contents(cr), number, got, a);
                                                    hot = 0;
                                                } else {
                                                    hot = got;
                                                }
                                            } else {
                                                borrow mut groups as &!gw in {
                                                    let (f2, e2, k2) = engine.group_fast(heap, gw, escr, kept, tree, contents(kr), line, contents(cr), number, a);
                                                    hot = f2;
                                                    escr = e2;
                                                    kept = k2;
                                                }
                                            }
                                            if hot == 1 || hot == 3 {
                                                let (g2, e2, k2) = engine.process_groups(heap, groups, escr, kept, tree, contents(kr), line, contents(cr), number, max_groups, max_distinct, max_state, hot == 3, a);
                                                groups = g2;
                                                escr = e2;
                                                kept = k2;
                                            }
                                        }
                                    }
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
                        if !header || mode != 0 {
                            rec = buffer.append(heap, rec, whole);
                        }
                        var held = 0;
                        borrow rec as &rw in {
                            held = buffer.size(rw);
                        }
                        if bad {
                            a[engine.k_abort()] = 1;
                            a[engine.k_abort_line()] = number;
                            going = false;
                        } else if inside {
                            quoted = true;
                            if !header || mode != 0 {
                                rec = buffer.push(heap, rec, byte_of(10));
                            }
                            if held + 1 > cap {
                                a[engine.k_abort()] = 3;
                                if header {
                                    a[engine.k_abort()] = 7;
                                }
                                a[engine.k_abort_line()] = opened;
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
                                    a[engine.k_columns()] = vec.size(er);
                                }
                                if mode != 0 {
                                    var made = frame.none(heap);
                                    frame.drop(heap, made);
                                    borrow names as &nr in {
                                        borrow hends as &er in {
                                            made = frame.prepare(heap, tree, nr, er, a[engine.k_columns()], mode, delim, sort);
                                        }
                                    }
                                    let frame.Frame { fcols, fsel, fcells, fhead, flead, flabels, flends, fpicked, fabort, fbad, fsort_field, fsort_slot, fdesc } = made;
                                    unbox_slice(heap, cols);
                                    unbox_slice(heap, sel);
                                    unbox_slice(heap, cells);
                                    buffer.drop(heap, head);
                                    buffer.drop(heap, labels);
                                    vec.drop(heap, lends);
                                    cols = fcols;
                                    sel = fsel;
                                    cells = fcells;
                                    head = fhead;
                                    labels = flabels;
                                    lends = flends;
                                    picked = fpicked;
                                    a[engine.k_sort_field()] = fsort_field;
                                    a[engine.k_sort_slot()] = fsort_slot;
                                    a[engine.k_sort_desc()] = fdesc;
                                    if ordering && fabort == 0 {
                                        let nk = query.order_count(tree);
                                        var okeys = box_slice(heap, 3 * nk + 1, 0);
                                        borrow mut okeys as &!ow in {
                                            borrow cols as &cr in {
                                                var j = 0;
                                                while j < nk {
                                                    contents(ow)[3 * j] = contents(cr)[query.order_name(tree, j)];
                                                    contents(ow)[3 * j + 1] = query.order_flags(tree, j) % 2;
                                                    contents(ow)[3 * j + 2] = query.order_flags(tree, j) / 2;
                                                    j = j + 1;
                                                }
                                            }
                                        }
                                        borrow okeys as &okr in {
                                            groups = sorter.configure(groups, nk, wanted, max_sort_rows, max_state, contents(okr));
                                        }
                                        unbox_slice(heap, okeys);
                                    }
                                    if fabort != 0 {
                                        a[engine.k_abort()] = fabort;
                                        a[engine.k_bad_name()] = fbad;
                                        going = false;
                                    }
                                    if as_csv && fabort == 0 {
                                        rows = start_output(heap, rows, flead);
                                    } else {
                                        buffer.drop(heap, flead);
                                    }
                                }
                                // The rest of the file, by several threads, when that is asked for and
                                // worth it (par.ls): it leaves the tally, the output and the groups as
                                // this loop would have.
                                if mode != 0 && a[engine.k_abort()] == 0 && threads > 1 && !ordering && (mode == 2 || row_from == 0) {
                                    var size = 0;
                                    match file_size(file) {
                                        Done::Ok(n) => {
                                            size = n;
                                        }
                                        Done::Failed(reason) => {
                                            size = 0;
                                        }
                                    }
                                    var at = 0;
                                    var lines_so_far = 0;
                                    borrow r as &rr in {
                                        at = lines.consumed(rr);
                                        lines_so_far = lines.number(rr);
                                    }
                                    if size - at >= min_bytes && size - at > 0 {
                                        var pp = box_slice(heap, scan.p_count(), 0);
                                        borrow mut pp as &!pw in {
                                            let u = contents(pw);
                                            u[scan.p_delim()] = delim;
                                            u[scan.p_mode()] = mode;
                                            if as_csv {
                                                u[scan.p_csv()] = 1;
                                                u[scan.p_yield()] = 65536;
                                            }
                                            u[scan.p_picked()] = picked;
                                            u[scan.p_budget()] = budget;
                                            u[scan.p_limit()] = limit;
                                            u[scan.p_most()] = most;
                                            u[scan.p_from()] = row_from;
                                            u[scan.p_max_groups()] = max_groups;
                                            u[scan.p_max_distinct()] = max_distinct;
                                            u[scan.p_max_state()] = max_state;
                                            u[scan.p_cap()] = cap;
                                            u[scan.p_base_line()] = lines_so_far;
                                            u[scan.p_size()] = size;
                                            borrow mut cells as &!cw in {
                                                borrow cols as &kr in {
                                                    borrow sel as &sr in {
                                                        let (r2, s2, e2, k2, g2) = par.run(heap, io, file, fs, root, rel, full, tree, contents(kr), contents(sr), contents(cw), a, u, rows, scratch, escr, kept, groups, at, lines_so_far, size, threads, chunk);
                                                        rows = r2;
                                                        scratch = s2;
                                                        escr = e2;
                                                        kept = k2;
                                                        groups = g2;
                                                    }
                                                }
                                            }
                                        }
                                        unbox_slice(heap, pp);
                                        going = false;
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
                                    if mode != 0 {
                                        borrow mut cells as &!cw in {
                                            let (n, open, bad_quote) = reader.fields(record, delim, contents(cw), a[engine.k_columns()]);
                                            found = n;
                                            wrong = bad_quote || open;
                                        }
                                    }
                                    if wrong {
                                        a[engine.k_abort()] = 1;
                                        a[engine.k_abort_line()] = opened;
                                        going = false;
                                    } else if engine.count_row(a, found, opened, row_from) && mode != 0 {
                                        borrow cells as &cr in {
                                            borrow sel as &sr in {
                                                borrow cols as &kr in {
                                                    if row_mode == 1 {
                                                        let (r2, s2, e2, k2) = engine.process_rows(heap, io, rows, scratch, escr, kept, tree, contents(kr), record, contents(cr), contents(sr), picked, delim, as_csv, budget, opened, limit, a);
                                                        rows = r2;
                                                        scratch = s2;
                                                        escr = e2;
                                                        kept = k2;
                                                    } else {
                                                        var hot = 1;
                                                        if !fast_ok {
                                                            // a distinct count is not added in place: `process_groups`
                                                        } else if !filtering {
                                                            var got = 1;
                                                            borrow mut groups as &!gw in {
                                                                got = engine.group_plain(gw, tree, contents(kr), record, contents(cr));
                                                            }
                                                            if got >= 16 {
                                                                kept = engine.group_refused(heap, kept, tree, contents(kr), record, contents(cr), opened, got, a);
                                                                hot = 0;
                                                            } else {
                                                                hot = got;
                                                            }
                                                        } else {
                                                            borrow mut groups as &!gw in {
                                                                let (f2, e2, k2) = engine.group_fast(heap, gw, escr, kept, tree, contents(kr), record, contents(cr), opened, a);
                                                                hot = f2;
                                                                escr = e2;
                                                                kept = k2;
                                                            }
                                                        }
                                                        if hot == 1 || hot == 3 {
                                                            let (g2, e2, k2) = engine.process_groups(heap, groups, escr, kept, tree, contents(kr), record, contents(cr), opened, max_groups, max_distinct, max_state, hot == 3, a);
                                                            groups = g2;
                                                            escr = e2;
                                                            kept = k2;
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                if a[engine.k_stop()] == 1 {
                    going = false;
                }
            }
        }
        // No header at all: no column to name.
        if mode != 0 && !header && a[engine.k_abort()] == 0 {
            a[engine.k_abort()] = 5;
        }
        // The end of the input with a quote open: not a record.
        if quoted && a[engine.k_abort()] == 0 {
            a[engine.k_abort()] = 2;
            a[engine.k_abort_line()] = opened;
        }
        var finished = rows;
        var spare = scratch;
        if mode == 2 && a[engine.k_abort()] == 0 {
            borrow mut groups as &!gm in {
                agg.settle(gm, tree);
            }
            borrow groups as &gr in {
                let (r2, s2) = finish_groups(heap, io, finished, spare, gr, tree, as_csv, delim, from, limit, top, budget, a);
                finished = r2;
                spare = s2;
            }
        }
        if ordering && a[engine.k_abort()] == 0 {
            borrow groups as &hr in {
                borrow mut cells as &!cw in {
                    borrow sel as &sr in {
                        let (r2, s2) = finish_sort(heap, io, finished, spare, hr, contents(sr), contents(cw), picked, as_csv, delim, from, limit, top, budget, a);
                        finished = r2;
                        spare = s2;
                    }
                }
            }
        }
        agg.drop(heap, groups);
        buffer.drop(heap, escr);
        buffer.drop(heap, spare);
        buffer.drop(heap, out_rows);
        buffer.drop(heap, out_kept);
        out_rows = finished;
        out_kept = kept;
        c = Counts { columns: a[engine.k_columns()], records: a[engine.k_records()], ragged: a[engine.k_ragged()], first_row: a[engine.k_first_row()], first_line: a[engine.k_first_line()], first_found: a[engine.k_first_found()], capped: a[engine.k_capped()] == 1, abort: a[engine.k_abort()], abort_line: a[engine.k_abort_line()], long_length: a[engine.k_long()], emitted: a[engine.k_emitted()], more: a[engine.k_more()] == 1, next: a[engine.k_next()], bad_name: a[engine.k_bad_name()], err_col: a[engine.k_err_col()], err_fn: a[engine.k_err_fn()], err_row: a[engine.k_err_row()], err_line: a[engine.k_err_line()], err_cond: a[engine.k_err_cond()], err_digits: a[engine.k_err_digits()], groups: a[engine.k_groups()] };
    }
    unbox_slice(heap, tally);
    unbox_slice(heap, cells);
    unbox_slice(heap, sel);
    unbox_slice(heap, cols);
    buffer.drop(heap, rec);
    var errno = 0;
    borrow r as &rr in {
        errno = lines.failed(rr);
    }
    lines.drop(heap, r);
    // What csv still had pending, written now unless the stream already failed.
    if as_csv && c.abort != 9 {
        let (after, ok) = engine.flush(io, out_rows);
        out_rows = after;
        if !ok {
            c = Counts { columns: c.columns, records: c.records, ragged: c.ragged, first_row: c.first_row, first_line: c.first_line, first_found: c.first_found, capped: c.capped, abort: 9, abort_line: 0, long_length: 0, emitted: c.emitted, more: c.more, next: c.next, bad_name: c.bad_name, err_col: 0, err_fn: 0, err_row: 0, err_line: 0, err_cond: 0, err_digits: 0, groups: c.groups };
        }
    }
    borrow names as &nr in {
        borrow hends as &er3 in {
            borrow labels as &lr in {
                borrow lends as &le in {
                    borrow out_kept as &kr in {
                        e = explain(heap, args, parsed, tree, nr, er3, lr, le, kr, c, shown, names_at, errno, as_csv, budget, e);
                    }
                }
            }
        }
    }
    buffer.drop(heap, labels);
    vec.drop(heap, lends);
    buffer.drop(heap, out_kept);
    return (c, head, out_rows, names, hends, e);
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

// The answer for rows or groups as json: `{"columns", "rows", "row_count",
// "truncated", "next"}`, and for groups also `"group_count"`.
fn report_select[&h, &a, &b](heap: &!h Heap, c: Counts, head: &a buffer.Buffer, rows: &b buffer.Buffer, grouped: bool) -> [heap] buffer.Buffer {
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
        o = buffer.append(heap, o, "}");
    } else {
        o = buffer.append(heap, o, "null");
    }
    if grouped {
        o = buffer.append(heap, o, ",\"group_count\":");
        o = buffer.push_nat(heap, o, c.groups);
    }
    return buffer.push(heap, o, byte_of('}'));
}

// A list of names read with `plan.parse`, in the plan: slot 0 for --select,
// slot 2 for --group. Refusals are added to `errs`.
fn add_names[&h, &g](heap: &!h Heap, tree: query.Query, errs: fail.Errors, given: &g [byte], slot: int, flag: &static [byte]) -> [heap] (query.Query, fail.Errors) {
    var e = errs;
    let (toks, ends, kinds, listed) = plan.parse(heap, given);
    var named = 0;
    borrow ends as &ter in {
        named = vec.size(ter);
    }
    if !listed {
        e = flag_problem(heap, e, "args.bad-value", "a list of names is names separated by commas, and a backslash escapes only a comma, a backslash or a #", "--select 'a,b\\,c'", flag);
    } else if named > plan.most_names() {
        e = flag_problem(heap, e, "args.bad-value", "a list names more columns than the ceiling", "4096 is the most one flag names", flag);
    }
    var q = tree;
    if listed {
        borrow toks as &tr in {
            borrow ends as &er in {
                borrow kinds as &kr in {
                    q = plan.fill(heap, q, tr, er, kr);
                }
            }
        }
        q = query.bump(q, slot, named);
    }
    buffer.drop(heap, toks);
    vec.drop(heap, ends);
    vec.drop(heap, kinds);
    return (q, e);
}

// `--order-by KEYS` as names (added to the plan after the aggregates') and flags: each key is `[-]NAME[:int]`. A leading
// `-` is descending, a trailing `:int` is exact integers; `\-` at the start and `\:` anywhere write a minus and a colon
// that are part of a name. What is left is a list of names as `--select`'s is, and goes through the same parser.
fn add_order_keys[&h, &g](heap: &!h Heap, tree: query.Query, errs: fail.Errors, given: &g [byte]) -> [heap] (query.Query, fail.Errors) {
    var e = errs;
    var cleaned = buffer.empty(heap, len(given) + 1);
    var flags = vec.empty(heap, 4, 0);
    let n = len(given);
    var i = 0;
    var at_start = true;
    var minus_done = false;
    var flag = 0;
    while i <= n {
        var c = -1;
        if i < n {
            c = int_of(given[i]);
        }
        let was_start = at_start;
        at_start = false;
        if c == ',' || c < 0 {
            flags = vec.push(heap, flags, flag);
            flag = 0;
            at_start = true;
            minus_done = false;
            if c == ',' {
                cleaned = buffer.push(heap, cleaned, byte_of(','));
            }
            i = i + 1;
        } else if was_start && c == '-' && !minus_done {
            flag = 1;
            minus_done = true;
            at_start = true;
            i = i + 1;
        } else if c == '\\' && i + 1 < n {
            let d = int_of(given[i + 1]);
            if d == '-' && was_start || d == ':' {
                cleaned = buffer.push(heap, cleaned, byte_of(d));
            } else {
                cleaned = buffer.push(heap, cleaned, byte_of(c));
                cleaned = buffer.push(heap, cleaned, byte_of(d));
            }
            i = i + 2;
        } else if c == ':' && i + 3 < n + 0 && int_of(given[i + 1]) == 'i' && int_of(given[i + 2]) == 'n' && int_of(given[i + 3]) == 't' && (i + 4 == n || int_of(given[i + 4]) == ',') {
            flag = flag + 2;
            i = i + 4;
        } else {
            cleaned = buffer.push(heap, cleaned, byte_of(c));
            i = i + 1;
        }
    }
    var toks = buffer.empty(heap, 1);
    var ends = vec.empty(heap, 1, 0);
    var kinds = vec.empty(heap, 1, 0);
    var listed = true;
    buffer.drop(heap, toks);
    vec.drop(heap, ends);
    vec.drop(heap, kinds);
    borrow cleaned as &cr in {
        let (t2, e2, k2, l2) = plan.parse(heap, buffer.bytes(cr));
        toks = t2;
        ends = e2;
        kinds = k2;
        listed = l2;
    }
    var named = 0;
    borrow ends as &ter in {
        named = vec.size(ter);
    }
    var q = tree;
    if !listed {
        e = flag_problem(heap, e, "args.bad-value", "a list of keys is names separated by commas, each with - before it for descending and :int after it for integers, and a backslash escapes only a comma, a backslash, a #, a leading - or a :", "--order-by '-bytes:int,status'", "--order-by");
    } else if named > plan.most_names() {
        e = flag_problem(heap, e, "args.bad-value", "a list names more columns than the ceiling", "4096 is the most one flag names", "--order-by");
    } else {
        var first = 0;
        borrow q as &qr in {
            first = query.name_count(qr);
        }
        borrow toks as &tr in {
            borrow ends as &er in {
                borrow kinds as &kr in {
                    q = plan.fill(heap, q, tr, er, kr);
                }
            }
        }
        var k = 0;
        while k < named {
            var f = 0;
            borrow flags as &fr in {
                f = vec.get(fr, k);
            }
            q = query.add_order(heap, q, first + k, f);
            k = k + 1;
        }
    }
    buffer.drop(heap, cleaned);
    buffer.drop(heap, toks);
    vec.drop(heap, ends);
    vec.drop(heap, kinds);
    vec.drop(heap, flags);
    return (q, e);
}

fn body[&h, &g, &p, &f, &i](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, errs: fail.Errors) -> [heap, args, fs_read(""), dir_read, file_read, io_write, err_write, conc] int {
    let table = flag_table();
    var e = errs;
    let form = cli.text(args, parsed, table, "format");
    let text_mode = bytes.equal(form, "text");
    let as_csv = bytes.equal(form, "csv");
    let has_select = cli.has(parsed, table, "select");
    let has_where = cli.has(parsed, table, "where");
    let has_group = cli.has(parsed, table, "group");
    let has_agg = cli.has(parsed, table, "agg");
    let has_order = cli.has(parsed, table, "order-by");
    let grouped = has_group || has_agg;
    var mode = 0;
    if grouped {
        mode = 2;
    } else if has_select || has_where || has_order {
        mode = 1;
    }
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
    let groups_most = cli.nat(args, parsed, table, "max-groups");
    if groups_most < 1 || groups_most > 1000000 {
        e = flag_problem(heap, e, "args.bad-value", "--max-groups is from 1 to 1000000", "--max-groups 100000", "--max-groups");
    }
    let distinct_most = cli.nat(args, parsed, table, "max-distinct");
    if distinct_most < 1 || distinct_most > 10000000 {
        e = flag_problem(heap, e, "args.bad-value", "--max-distinct is from 1 to 10000000", "--max-distinct 100000", "--max-distinct");
    }
    let state_most = cli.nat(args, parsed, table, "max-state-bytes");
    if state_most < 1 || state_most > 1073741824 {
        e = flag_problem(heap, e, "args.bad-value", "--max-state-bytes is from 1 to 1073741824", "--max-state-bytes 67108864", "--max-state-bytes");
    }
    let sort_rows_most = cli.nat(args, parsed, table, "max-sort-rows");
    if sort_rows_most < 1 || sort_rows_most > 20000000 {
        e = flag_problem(heap, e, "args.bad-value", "--max-sort-rows is from 1 to 20000000", "--max-sort-rows 1000000", "--max-sort-rows");
    }
    let thread_count = cli.nat(args, parsed, table, "threads");
    if thread_count < 1 || thread_count > 64 {
        e = flag_problem(heap, e, "args.bad-value", "--threads is from 1 to 64", "--threads 4", "--threads");
    }
    let chunk_size = cli.nat(args, parsed, table, "chunk-bytes");
    if chunk_size < 1 || chunk_size > 1073741824 {
        e = flag_problem(heap, e, "args.bad-value", "--chunk-bytes is from 1 to 1073741824", "--chunk-bytes 4194304", "--chunk-bytes");
    }
    if cli.nat(args, parsed, table, "parallel-min-bytes") > 1073741824 {
        e = flag_problem(heap, e, "args.bad-value", "--parallel-min-bytes is at most 1073741824", "--parallel-min-bytes 1048576", "--parallel-min-bytes");
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
    var top = 0;
    if cli.has(parsed, table, "limit") {
        limit = cli.nat(args, parsed, table, "limit");
        if limit < 1 || limit > 1000000 {
            e = flag_problem(heap, e, "args.bad-value", "--limit is from 1 to 1000000", "--limit 1000", "--limit");
        }
    }
    if cli.has(parsed, table, "from") {
        from = cli.nat(args, parsed, table, "from");
    }
    if cli.has(parsed, table, "top") {
        top = cli.nat(args, parsed, table, "top");
        if top < 1 {
            e = flag_problem(heap, e, "args.bad-value", "--top is at least 1", "--top 10", "--top");
        }
    }
    if mode == 0 {
        if as_csv {
            e = flag_problem(heap, e, "args.required-flag", "--format csv writes rows or groups, so it needs --select, --where or --group", "--select NAMES", "--format --select --where --group");
        }
        if cli.has(parsed, table, "limit") || cli.has(parsed, table, "from") {
            e = flag_problem(heap, e, "args.required-flag", "--limit and --from page rows or groups, so they need --select, --where or --group", "--select NAMES", "--limit --from --select");
        }
    } else if text_mode {
        e = flag_problem(heap, e, "args.conflict", "--format text is for the shape; rows and groups are json or csv", "--format csv", "--format --select --where --group");
    }
    if grouped && has_select {
        e = flag_problem(heap, e, "args.conflict", "--select picks columns, and with --group or --agg the columns are the groups and the aggregates", "drop --select", "--select --group --agg");
    }
    if grouped && has_order {
        e = flag_problem(heap, e, "args.conflict", "--order-by sorts rows, and with --group or --agg the rows are counted into groups: order those with --sort and --top", "drop --order-by", "--order-by --group --agg");
    }
    if !grouped && (cli.has(parsed, table, "sort") || cli.has(parsed, table, "top") && !has_order) {
        e = flag_problem(heap, e, "args.required-flag", "--sort orders groups and --top cuts groups or sorted rows, so they need --group, --agg or --order-by", "--group NAMES", "--sort --top --group");
    }
    // The plan.
    var tree = query.empty(heap);
    if has_select {
        let (t2, e2) = add_names(heap, tree, e, cli.text(args, parsed, table, "select"), 0, "--select");
        tree = t2;
        e = e2;
    }
    if has_where {
        let given = cli.text(args, parsed, table, "where");
        let (t2, what, at) = expr.parse(heap, tree, given);
        tree = t2;
        if what != 0 {
            var w = fail.open_in(heap, extra(), "where.syntax", "--where is not an expression", "see docs/filter.md for the grammar: COLUMN OP VALUE joined by and");
            w = fail.repair_none(heap, w, "what the expression was meant to say is not known");
            w = fail.detail_open(heap, w);
            w = fail.detail_int(heap, w, "offset", at);
            w = fail.detail_str(heap, w, "expected", expr.expected(what));
            w = fail.detail_text(heap, w, "expression", given);
            e = fail.add(heap, e, w);
        }
    }
    if has_group {
        let (t2, e2) = add_names(heap, tree, e, cli.text(args, parsed, table, "group"), 2, "--group");
        tree = t2;
        e = e2;
    }
    if grouped {
        if has_agg {
            let given = cli.text(args, parsed, table, "agg");
            let (t2, bad) = agg.parse_aggs(heap, tree, given);
            tree = t2;
            if bad == -2 {
                e = flag_problem(heap, e, "args.bad-value", "--agg is items separated by commas, and a backslash escapes only a comma, a backslash or a #", "--agg count,sum:bytes", "--agg");
            } else if bad >= 0 {
                var w = fail.open_in(heap, extra(), "agg.bad-spec", "an item of --agg is not count, sum:COL, min:COL, max:COL, mean:COL or distinct:COL, or its :int, :dec(S) or @N is not one that item takes", "--agg count,sum:bytes,max:bytes,distinct:status; a mean of an integer column needs its scale, mean:bytes@2; a decimal column is declared sum:price:dec(2)");
                w = fail.no_repair(heap, w);
                w = fail.detail_open(heap, w);
                w = fail.detail_int(heap, w, "item", bad);
                w = fail.detail_str(heap, w, "agg", given);
                e = fail.add(heap, e, w);
            }
        } else {
            tree = query.add_agg(heap, tree, 0, -1, 0, 0);
        }
    }
    if has_order && !grouped {
        let (t2, e2) = add_order_keys(heap, tree, e, cli.text(args, parsed, table, "order-by"));
        tree = t2;
        e = e2;
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
    if has_select && cli.value_is_whole(parsed, table, "select") {
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
                                borrow tree as &tq in {
                                    let (c, head, rows, names, hends, after) = read_file(heap, args, parsed, handle, path.shown(tp), io, fs, buffer.bytes(rr), path.shown(tp), path.full(tp), delim, mode, as_csv, from, limit, budget, top, tq, names_at, e);
                                    e = after;
                                    // What was read is the answer unless the read had to
                                    // stop short of the end: a ragged row is an error
                                    // about rows that were all counted.
                                    streamed = as_csv;
                                    wrote = c.abort;
                                    if c.abort == 0 && !as_csv {
                                        buffer.drop(heap, payload);
                                        buffer.drop(heap, plain);
                                        if mode != 0 {
                                            borrow head as &hr in {
                                                borrow rows as &wr in {
                                                    payload = report_select(heap, c, hr, wr, mode == 2);
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
    query.drop(heap, tree);
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
                    status = out.respond(heap, io, "table", "table.v2", "0.3.0", buffer.bytes(d), "", er, text_mode, buffer.bytes(t), 0);
                }
            }
        }
    }
    buffer.drop(heap, payload);
    buffer.drop(heap, plain);
    fail.drop(heap, e);
    return status;
}

fn run[&h, &g, &f, &i](heap: &!h Heap, args: &g Args, fs: &f Fs(""), io: &!i Io) -> [heap, args, fs_read(""), dir_read, file_read, io_write, err_write, conc] int {
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

fn main(world: World) -> [conc] int {
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
