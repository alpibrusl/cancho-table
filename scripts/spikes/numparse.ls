// SPIKE (throwaway, docs/numbers.md sections A and B): what reading a decimal and a float cell costs next to
// the integer cell `table` reads today, and what summing them costs.
//
//     numparse MODE ROUNDS < cells.txt        one cell per line; the program loops over the cells ROUNDS times
//
//   MODE  what a round does with every cell
//   line  finds the line ends and adds the lengths               (the floor: the reading cost every mode shares)
//   int   `parse_int` of query.ls (copied) + the checked 64-bit add of agg.ls
//   dec2  `parse_dec` at scale 2 + the pair-limb sum that cannot overflow (hi * 2^32 + lo, carried when lo leaves 2^62)
//   fast  the float scanner of this file: digits, point, exponent, Clinger's fast path inline, then a plain f64 add
//   D/F   `numparse D SCALE` / `numparse F`: print `status value` (F: the double's bits) for every cell, for the differential
//         check against numbers_ref.py (numparse_check.py)
//   dec0  parse_dec at scale 0 (to compare with `int` on integer cells: same digits, the dec grammar and the pair sum)
//   json  the same cell through std.json (`parse` of a one-number document + `to_float`: the language's
//         correctly rounded reader, the only one it has) then a plain f64 add
//   facc  the float scanner + the superaccumulator add (superacc.ls) -- the full proposed float-sum path
edition 5;

import std.buffer;
import std.io;
import std.json;

fn read_stdin[&h, &i](heap: &!h Heap, io: &!i Io, text: buffer.Buffer) -> [heap, io_read] buffer.Buffer {
    var out = text;
    var c = getchar(io);
    while c >= 0 {
        out = buffer.push(heap, out, byte_of(c));
        c = getchar(io);
    }
    return out;
}

fn number_of[&s](text: &s [byte]) -> [] int {
    var n = 0;
    var i = 0;
    while i < len(text) {
        n = n * 10 + (int_of(text[i]) - '0');
        i = i + 1;
    }
    return n;
}

// ---- the integer path, copied from tools/table/query.ls and agg.ls ---------------------------------------------

fn int_min() -> [] int {
    return 0 - 9223372036854775807 - 1;
}

fn parse_int[&d](data: &d [byte]) -> [] (int, int) {
    var at = 0;
    var negative = false;
    if len(data) > 0 && (int_of(data[0]) == '-' || int_of(data[0]) == '+') {
        negative = int_of(data[0]) == '-';
        at = 1;
    }
    if at >= len(data) {
        return (0, 1);
    }
    if len(data) - at <= 18 {
        var plain = 0;
        while at < len(data) {
            let c = int_of(data[at]);
            if c < '0' || c > '9' {
                return (0, 1);
            }
            plain = plain * 10 + c - '0';
            at = at + 1;
        }
        if negative {
            return (0 - plain, 0);
        }
        return (plain, 0);
    }
    return (0, 2);
}

fn add_checked(a: int, b: int) -> [] (int, bool) {
    if b > 0 && a > 9223372036854775807 - b {
        return (0, false);
    }
    if b < 0 && a < int_min() - b {
        return (0, false);
    }
    return (a + b, true);
}

// ---- the decimal path -------------------------------------------------------------------------------------------

fn pow10_int(k: int) -> [] int {
    var x = 1;
    var n = 0;
    while n < k {
        x = x * 10;
        n = n + 1;
    }
    return x;
}

// `[+-]? digits? ('.' digits?)?` with at least one digit, at most `scale` fractional digits, and a scaled value
// below 10^18 (so it is an `int` and a sum of 2^40 of them is not). Answers (scaled value, status): 0 ok; 1 not
// a decimal; 2 more than 18 significant digits once scaled; 3 more than `scale` fractional digits.
fn parse_dec[&d](data: &d [byte], scale: int) -> [] (int, int) {
    var at = 0;
    var negative = false;
    let n = len(data);
    if n > 0 && (int_of(data[0]) == '-' || int_of(data[0]) == '+') {
        negative = int_of(data[0]) == '-';
        at = 1;
    }
    var v = 0;
    var digits = 0;
    var frac = 0;
    var seen_point = false;
    var wide = false;
    while at < n {
        let c = int_of(data[at]);
        if c >= '0' && c <= '9' {
            if v >= 100000000000000000 {
                // too wide: remembered, not returned, because the rest of the cell is still to be checked (a grammar
                // error outranks a width error, and a scale error too: the order is fixed in docs/numbers.md)
                wide = true;
            } else {
                v = v * 10 + c - '0';
            }
            digits = digits + 1;
            if seen_point {
                frac = frac + 1;
            }
        } else if c == '.' && !seen_point {
            seen_point = true;
        } else {
            return (0, 1);
        }
        at = at + 1;
    }
    if digits == 0 {
        return (0, 1);
    }
    if frac > scale {
        return (0, 3);
    }
    if wide {
        return (0, 2);
    }
    if frac < scale {
        let pad = scale - frac;
        if v > 999999999999999999 / pow10_int(pad) {
            return (0, 2);
        }
        v = v * pow10_int(pad);
    }
    if negative {
        return (0 - v, 0);
    }
    return (v, 0);
}

// ---- the float path ---------------------------------------------------------------------------------------------

static pow10_float: [float] {
    let t = alloc_slice[static](23, 1.0);
    var k = 1;
    while k < 23 {
        t[k] = t[k - 1] * 10.0;
        k = k + 1;
    }
    return t;
}

// `[+-]? digits? ('.' digits?)? ([eE] [+-]? digits)?` with at least one mantissa digit. Answers (value, status):
// 0 ok (fast path or not decided: status 4 = "valid, not decided by Clinger's path": the caller hands it to the exact
// reader); 1 not a number. The fast path: at most 15 significant digits... here the mantissa is at most 2^53 and
// |e10| <= 22, so `m` and `10^|e10|` are doubles and one correctly rounded operation is exact.
fn scan_float[&d](data: &d [byte]) -> [] (float, int) {
    var at = 0;
    var negative = false;
    let n = len(data);
    if n > 0 && (int_of(data[0]) == '-' || int_of(data[0]) == '+') {
        negative = int_of(data[0]) == '-';
        at = 1;
    }
    var m = 0;
    var digits = 0;
    var e10 = 0;
    var seen_point = false;
    var inexact = false;
    var scanning = true;
    while at < n && scanning {
        let c = int_of(data[at]);
        if c >= '0' && c <= '9' {
            if m < 900719925474099 {
                m = m * 10 + c - '0';
                if seen_point {
                    e10 = e10 - 1;
                }
            } else {
                inexact = true;
            }
            digits = digits + 1;
        } else if c == '.' && !seen_point {
            seen_point = true;
        } else {
            scanning = false;
        }
        if scanning {
            at = at + 1;
        }
    }
    if digits == 0 {
        return (0.0, 1);
    }
    if at < n {
        let c = int_of(data[at]);
        if c != 'e' && c != 'E' {
            return (0.0, 1);
        }
        at = at + 1;
        var eneg = false;
        if at < n && (int_of(data[at]) == '-' || int_of(data[at]) == '+') {
            eneg = int_of(data[at]) == '-';
            at = at + 1;
        }
        if at >= n {
            return (0.0, 1);
        }
        var ex = 0;
        while at < n {
            let c2 = int_of(data[at]);
            if c2 < '0' || c2 > '9' {
                return (0.0, 1);
            }
            if ex < 100000 {
                ex = ex * 10 + c2 - '0';
            }
            at = at + 1;
        }
        if eneg {
            e10 = e10 - ex;
        } else {
            e10 = e10 + ex;
        }
    }
    if inexact || e10 < 0 - 22 || e10 > 22 {
        return (0.0, 4);
    }
    var x = float_of(m);
    if e10 < 0 {
        x = x / pow10_float[0 - e10];
    } else {
        x = x * pow10_float[e10];
    }
    if negative {
        x = 0.0 - x;
    }
    return (x, 0);
}

// ---- the exact accumulator (superacc.ls), the add only ----------------------------------------------------------

fn facc_add[&a](acc: &!a [int], x: float) -> [] int {
    let bits = bits_of(x);
    let e = bits >> 52 & 0x7ff;
    var m = bits & 0xfffffffffffff;
    var p = 0;
    if e != 0 {
        m = m | 0x10000000000000;
        p = e - 1;
    }
    let i = p >> 5;
    let s = p & 31;
    let a = (m & 0xffffffff) << s;
    let b = (m >> 32) << s;
    let c0 = a & 0xffffffff;
    let c1 = (a >> 32) + (b & 0xffffffff);
    let c2 = b >> 32;
    if bits < 0 {
        acc[i] = acc[i] - c0;
        acc[i + 1] = acc[i + 1] - c1;
        acc[i + 2] = acc[i + 2] - c2;
    } else {
        acc[i] = acc[i] + c0;
        acc[i + 1] = acc[i + 1] + c1;
        acc[i + 2] = acc[i + 2] + c2;
    }
    let n = acc[72] + 1;
    if n >= 134217728 {
        var carry = 0;
        var j = 0;
        while j < 71 {
            let v = acc[j] + carry;
            acc[j] = v & 0xffffffff;
            carry = v >> 32;
            j = j + 1;
        }
        acc[71] = acc[71] + carry;
        acc[72] = 0;
    } else {
        acc[72] = n;
    }
    return 0;
}

fn show[&s, &i](io: &!i Io, src: &s [byte], mode: int, scale: int) -> [io_write] int {
    var at = 0;
    while at < len(src) {
        var stop = index_of_byte(src[at..len(src)], byte_of(10));
        if stop < 0 {
            stop = len(src) - at;
        }
        let cell = src[at..at + stop];
        at = at + stop + 1;
        if mode == 7 {
            let (v, st) = parse_dec(cell, scale);
            io.print_int(io, st);
            io.space(io);
            io.print_int(io, v);
        } else {
            let (x, st) = scan_float(cell);
            io.print_int(io, st);
            io.space(io);
            io.print_int(io, bits_of(x));
        }
        io.newline(io);
    }
    return 0;
}

fn run[&s](src: &s [byte], mode: int, rounds: int) -> [] int {
    var hi = 0;
    var lo = 0;
    var total = 0;
    var fsum = 0.0;
    var bad = 0;
    var ints = 0;
    region r {
        let acc = alloc_slice[r](73, 0);
        let tape = alloc_slice[r](12, 0);
        var round = 0;
        while round < rounds {
            var at = 0;
            while at < len(src) {
                var stop = index_of_byte(src[at..len(src)], byte_of(10));
                if stop < 0 {
                    stop = len(src) - at;
                }
                let cell = src[at..at + stop];
                at = at + stop + 1;
                if mode == 0 {
                    total = total + len(cell);
                } else if mode == 1 {
                    let (v, st) = parse_int(cell);
                    if st != 0 {
                        bad = bad + 1;
                    } else {
                        let (sum, fits) = add_checked(total, v);
                        if fits {
                            total = sum;
                        } else {
                            bad = bad + 1;
                        }
                    }
                } else if mode == 2 || mode == 6 {
                    var sc = 2;
                    if mode == 6 {
                        sc = 0;
                    }
                    let (v, st) = parse_dec(cell, sc);
                    if st != 0 {
                        bad = bad + 1;
                    } else {
                        lo = lo + v;
                        if lo > 4611686018427387904 || lo < 0 - 4611686018427387904 {
                            hi = hi + (lo >> 32);
                            lo = lo & 0xffffffff;
                        }
                    }
                } else if mode == 3 || mode == 5 {
                    let (x, st) = scan_float(cell);
                    if st != 0 {
                        bad = bad + 1;
                    } else if mode == 3 {
                        fsum = fsum + x;
                    } else {
                        facc_add(acc, x);
                    }
                } else if mode == 4 {
                    let nodes = json.parse(cell, tape);
                    if nodes < 0 {
                        bad = bad + 1;
                    } else {
                        fsum = fsum + json.to_float(cell, tape, 0);
                    }
                }
            }
            round = round + 1;
        }
    }
    ints = lo + hi * 4294967296;
    return bad * 1000000007 + (ints + total + truncate(fsum)) % 1000000007;
}

fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock } = split(world);
    release(fs);
    release(ffi);
    release(net);
    release(clock);
    var mode = 0;
    var rounds = 1;
    borrow args as &g in {
        let a1 = arg(g, 1);
        if int_of(a1[0]) == 'l' {
            mode = 0;
        } else if int_of(a1[0]) == 'i' {
            mode = 1;
        } else if int_of(a1[0]) == 'd' {
            mode = 2;
        } else if int_of(a1[0]) == 'f' {
            mode = 3;
        } else if int_of(a1[0]) == 'j' {
            mode = 4;
        } else if int_of(a1[0]) == 'e' {
            mode = 6;
        } else if int_of(a1[0]) == 'D' {
            mode = 7;
        } else if int_of(a1[0]) == 'F' {
            mode = 8;
        } else {
            mode = 5;
        }
        rounds = number_of(arg(g, 2));
    }
    release(args);
    var status = 0;
    borrow mut heap as &!h in {
        borrow mut io as &!i in {
            var text = buffer.empty(h, 1048576);
            text = read_stdin(h, i, text);
            borrow text as &b in {
                if mode >= 7 {
                    show(i, buffer.bytes(b), mode, rounds);
                } else {
                    let answer = run(buffer.bytes(b), mode, rounds);
                    io.print_int(i, answer);
                    io.newline(i);
                }
            }
            buffer.drop(h, text);
        }
    }
    release(heap);
    release(io);
    return status;
}
