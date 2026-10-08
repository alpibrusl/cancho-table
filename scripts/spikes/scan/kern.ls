edition 5;

// Scan kernels over a whole file held in memory: how fast can a record be cut into fields?
//   kern PATH V ROUNDS     V = 0 production (memchr for the line + reader.fields)
//                          1 one pass over the bytes, a byte loop (no memchr)
//                          2 one pass, SWAR words for the delimiter / newline / quote search
//                          3 stage 1 (SWAR token index per 32 KiB block) + stage 2 (a walk over the tokens)
//                          9 memchr for the newline only (the floor of the line split)
// Every variant answers the same checksum on a well formed file.

import std.io;
import reader;

fn v0[&t, &c](text: &t [byte], n: int, cells: &!c [int]) -> [] int {
    var at = 0;
    var sum = 0;
    var recs = 0;
    while at < n {
        let found = index_of_byte(text[at..n], byte_of(10));
        var k = n;
        if found >= 0 {
            k = at + found;
        }
        let line = text[at..k];
        let (m, open, bad) = reader.fields(line, 44, cells, 8);
        var j = 0;
        while j < m && j < 8 {
            sum = sum + cells[3 * j + 1] - cells[3 * j] + cells[3 * j + 2];
            j = j + 1;
        }
        sum = sum + m;
        recs = recs + 1;
        at = k + 1;
    }
    return sum + recs;
}

fn v9[&t](text: &t [byte], n: int) -> [] int {
    var at = 0;
    var sum = 0;
    while at < n {
        let found = index_of_byte(text[at..n], byte_of(10));
        var k = n;
        if found >= 0 {
            k = at + found;
        }
        sum = sum + (k - at);
        at = k + 1;
    }
    return sum;
}

fn v1[&t, &c](text: &t [byte], n: int, cells: &!c [int]) -> [] int {
    var p = 0;
    var sum = 0;
    var recs = 0;
    while p < n {
        var nf = 0;
        var more = true;
        while more {
            var s = p;
            var e = p;
            var q = 0;
            if p < n && int_of(text[p]) == 34 {
                var i = p + 1;
                var closed = false;
                while !closed && i < n {
                    if int_of(text[i]) == 34 {
                        if i + 1 < n && int_of(text[i + 1]) == 34 {
                            i = i + 2;
                        } else {
                            closed = true;
                        }
                    } else {
                        i = i + 1;
                    }
                }
                s = p + 1;
                e = i;
                q = 1;
                p = i + 1;
            } else {
                var i = p;
                while i < n && int_of(text[i]) != 44 && int_of(text[i]) != 10 {
                    i = i + 1;
                }
                e = i;
                p = i;
            }
            if nf < 8 {
                cells[3 * nf] = s;
                cells[3 * nf + 1] = e;
                cells[3 * nf + 2] = q;
            }
            nf = nf + 1;
            sum = sum + e - s + q;
            if p < n && int_of(text[p]) == 44 {
                p = p + 1;
            } else {
                p = p + 1;
                more = false;
            }
        }
        recs = recs + 1;
        sum = sum + nf;
    }
    return sum + recs;
}

// The SWAR search is written out in each loop: a function that builds the word from eight loads is not inlined, and
// then the eight bounds checks stay (see docs/gap-scan.md), while in the loop with `i + 8 <= n` they fold into one load.
fn v2[&t, &c](text: &t [byte], n: int, cells: &!c [int]) -> [] int {
    var p = 0;
    var sum = 0;
    var recs = 0;
    while p < n {
        var nf = 0;
        var more = true;
        while more {
            var s = p;
            var e = p;
            var q = 0;
            if p < n && int_of(text[p]) == 34 {
                var i = p + 1;
                var closed = false;
                while !closed && i < n {
                    // the next quote
                    var hit = false;
                    while !hit && i < n {
                        if i + 8 <= n {
                            let w = int_of(text[i]) | (int_of(text[i + 1]) << 8) | (int_of(text[i + 2]) << 16) | (int_of(text[i + 3]) << 24) | (int_of(text[i + 4]) << 32) | (int_of(text[i + 5]) << 40) | (int_of(text[i + 6]) << 48) | (int_of(text[i + 7]) << 56);
                            let x = w ^ 0x2222222222222222;
                            let z = ~(wrapping_add(x & 0x7f7f7f7f7f7f7f7f, 0x7f7f7f7f7f7f7f7f) | x | 0x7f7f7f7f7f7f7f7f);
                            if z == 0 {
                                i = i + 8;
                            } else {
                                let low = z & wrapping_sub(0, z);
                                let b = (low >> 7) & 0x0101010101010101;
                                i = i + ((wrapping_mul(b, 0x0001020304050607) >> 56) & 255);
                                hit = true;
                            }
                        } else if int_of(text[i]) == 34 {
                            hit = true;
                        } else {
                            i = i + 1;
                        }
                    }
                    if i < n {
                        if i + 1 < n && int_of(text[i + 1]) == 34 {
                            i = i + 2;
                        } else {
                            closed = true;
                        }
                    }
                }
                s = p + 1;
                e = i;
                q = 1;
                p = i + 1;
            } else {
                var i = p;
                var hit = false;
                while !hit && i < n {
                    if i + 8 <= n {
                        let w = int_of(text[i]) | (int_of(text[i + 1]) << 8) | (int_of(text[i + 2]) << 16) | (int_of(text[i + 3]) << 24) | (int_of(text[i + 4]) << 32) | (int_of(text[i + 5]) << 40) | (int_of(text[i + 6]) << 48) | (int_of(text[i + 7]) << 56);
                        let x = w ^ 0x2c2c2c2c2c2c2c2c;
                        let y = w ^ 0x0a0a0a0a0a0a0a0a;
                        let z = ~(wrapping_add(x & 0x7f7f7f7f7f7f7f7f, 0x7f7f7f7f7f7f7f7f) | x | 0x7f7f7f7f7f7f7f7f) | ~(wrapping_add(y & 0x7f7f7f7f7f7f7f7f, 0x7f7f7f7f7f7f7f7f) | y | 0x7f7f7f7f7f7f7f7f);
                        if z == 0 {
                            i = i + 8;
                        } else {
                            let low = z & wrapping_sub(0, z);
                            let b = (low >> 7) & 0x0101010101010101;
                            i = i + ((wrapping_mul(b, 0x0001020304050607) >> 56) & 255);
                            hit = true;
                        }
                    } else if int_of(text[i]) == 44 || int_of(text[i]) == 10 {
                        hit = true;
                    } else {
                        i = i + 1;
                    }
                }
                e = i;
                p = i;
            }
            if nf < 8 {
                cells[3 * nf] = s;
                cells[3 * nf + 1] = e;
                cells[3 * nf + 2] = q;
            }
            nf = nf + 1;
            sum = sum + e - s + q;
            if p < n && int_of(text[p]) == 44 {
                p = p + 1;
            } else {
                p = p + 1;
                more = false;
            }
        }
        recs = recs + 1;
        sum = sum + nf;
    }
    return sum + recs;
}

fn v3[&t, &c, &k](text: &t [byte], n: int, cells: &!c [int], pos: &!k [int]) -> [] int {
    let block = 32768;
    var base = 0;
    var fs = 0;
    var mode = 0;
    var skip = 0 - 1;
    var qend = 0;
    var qs = 0;
    var nf = 0;
    var recs = 0;
    var sum = 0;
    while base < n {
        var end = base + block;
        if end > n {
            end = n;
        }
        // stage 1: the positions of every delimiter, newline and quote of the block
        var m = 0;
        var i = base;
        while i + 8 <= end {
            let w = int_of(text[i]) | (int_of(text[i + 1]) << 8) | (int_of(text[i + 2]) << 16) | (int_of(text[i + 3]) << 24) | (int_of(text[i + 4]) << 32) | (int_of(text[i + 5]) << 40) | (int_of(text[i + 6]) << 48) | (int_of(text[i + 7]) << 56);
            let x = w ^ 0x2c2c2c2c2c2c2c2c;
            let y = w ^ 0x0a0a0a0a0a0a0a0a;
            let u = w ^ 0x2222222222222222;
            var z = ~(wrapping_add(x & 0x7f7f7f7f7f7f7f7f, 0x7f7f7f7f7f7f7f7f) | x | 0x7f7f7f7f7f7f7f7f) | ~(wrapping_add(y & 0x7f7f7f7f7f7f7f7f, 0x7f7f7f7f7f7f7f7f) | y | 0x7f7f7f7f7f7f7f7f) | ~(wrapping_add(u & 0x7f7f7f7f7f7f7f7f, 0x7f7f7f7f7f7f7f7f) | u | 0x7f7f7f7f7f7f7f7f);
            while z != 0 {
                let low = z & wrapping_sub(0, z);
                let b = (low >> 7) & 0x0101010101010101;
                pos[m] = i + ((wrapping_mul(b, 0x0001020304050607) >> 56) & 255);
                m = m + 1;
                z = z ^ low;
            }
            i = i + 8;
        }
        while i < end {
            let c = int_of(text[i]);
            if c == 44 || c == 10 || c == 34 {
                pos[m] = i;
                m = m + 1;
            }
            i = i + 1;
        }
        // stage 2: the tokens, by the grammar
        var j = 0;
        while j < m {
            let at = pos[j];
            let c = int_of(text[at]);
            var s = 0;
            var e = 0;
            var q = 0;
            var emit = false;
            if mode == 0 {
                if c == 34 {
                    if at == fs {
                        mode = 1;
                        qs = at + 1;
                    }
                } else {
                    s = fs;
                    e = at;
                    emit = true;
                }
            } else if mode == 1 {
                if c == 34 {
                    if at == skip {
                        skip = 0 - 1;
                    } else if at + 1 < n && int_of(text[at + 1]) == 34 {
                        skip = at + 1;
                    } else {
                        qend = at;
                        mode = 2;
                    }
                }
            } else {
                s = qs;
                e = qend;
                q = 1;
                emit = true;
                mode = 0;
            }
            if emit {
                if nf < 8 {
                    cells[3 * nf] = s;
                    cells[3 * nf + 1] = e;
                    cells[3 * nf + 2] = q;
                }
                nf = nf + 1;
                sum = sum + e - s + q;
                fs = at + 1;
                if c == 10 {
                    recs = recs + 1;
                    sum = sum + nf;
                    nf = 0;
                }
            }
            j = j + 1;
        }
        base = end;
    }
    return sum + recs;
}

fn v4[&t, &c, &k](text: &t [byte], n: int, cells: &!c [int], pos: &!k [int]) -> [] int {
    let block = 32768;
    var base = 0;
    var fs = 0;
    var mode = 0;
    var skip = 0 - 1;
    var qend = 0;
    var qs = 0;
    var nf = 0;
    var recs = 0;
    var sum = 0;
    while base < n {
        var end = base + block;
        if end > n {
            end = n;
        }
        // stage 1: the positions of every delimiter, newline and quote of the block
        var m = 0;
        var i = base;
        while i + 64 <= end {
            var z = byte_mask64(text, i, byte_of(44), byte_of(10), byte_of(34));
            while z != 0 {
                pos[m] = i + trailing_zeros(z);
                m = m + 1;
                z = z & wrapping_sub(z, 1);
            }
            i = i + 64;
        }
        while i < end {
            let c = int_of(text[i]);
            if c == 44 || c == 10 || c == 34 {
                pos[m] = i;
                m = m + 1;
            }
            i = i + 1;
        }
        // stage 2: the tokens, by the grammar
        var j = 0;
        while j < m {
            let at = pos[j];
            let c = int_of(text[at]);
            var s = 0;
            var e = 0;
            var q = 0;
            var emit = false;
            if mode == 0 {
                if c == 34 {
                    if at == fs {
                        mode = 1;
                        qs = at + 1;
                    }
                } else {
                    s = fs;
                    e = at;
                    emit = true;
                }
            } else if mode == 1 {
                if c == 34 {
                    if at == skip {
                        skip = 0 - 1;
                    } else if at + 1 < n && int_of(text[at + 1]) == 34 {
                        skip = at + 1;
                    } else {
                        qend = at;
                        mode = 2;
                    }
                }
            } else {
                s = qs;
                e = qend;
                q = 1;
                emit = true;
                mode = 0;
            }
            if emit {
                if nf < 8 {
                    cells[3 * nf] = s;
                    cells[3 * nf + 1] = e;
                    cells[3 * nf + 2] = q;
                }
                nf = nf + 1;
                sum = sum + e - s + q;
                fs = at + 1;
                if c == 10 {
                    recs = recs + 1;
                    sum = sum + nf;
                    nf = 0;
                }
            }
            j = j + 1;
        }
        base = end;
    }
    return sum + recs;
}

fn run[&h, &f, &g, &i, &k](heap: &!h Heap, fs: &f Fs(""), args: &g Args, io: &!i Io, clock: &k Clock) -> [args, fs_read(""), heap, io_write, clock] int {
    let path = arg(args, 1);
    let variant = int_of(arg(args, 2)[0]) - 48;
    let rounds = int_of(arg(args, 3)[0]) - 48;
    var text = box_slice(heap, 300000000, byte_of(0));
    var cells = box_slice(heap, 64, 0);
    var pos = box_slice(heap, 40000, 0);
    var n = 0;
    borrow mut text as &!tw in {
        n = fs_read(fs, path, contents(tw));
    }
    var r = 0;
    while r < rounds {
        let t0 = clock_ms(clock);
        var answer = 0;
        borrow text as &tr in {
            borrow mut cells as &!cw in {
                borrow mut pos as &!pw in {
                    if variant == 0 {
                        answer = v0(contents(tr), n, contents(cw));
                    } else if variant == 1 {
                        answer = v1(contents(tr), n, contents(cw));
                    } else if variant == 2 {
                        answer = v2(contents(tr), n, contents(cw));
                    } else if variant == 3 {
                        answer = v3(contents(tr), n, contents(cw), contents(pw));
                    } else if variant == 4 {
                        answer = v4(contents(tr), n, contents(cw), contents(pw));
                    } else {
                        answer = v9(contents(tr), n);
                    }
                }
            }
        }
        let t1 = clock_ms(clock);
        io.print_int(io, answer);
        io.space(io);
        io.print_int(io, t1 - t0);
        io.newline(io);
        r = r + 1;
    }
    unbox_slice(heap, text);
    unbox_slice(heap, cells);
    unbox_slice(heap, pos);
    return 0;
}

fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock } = split(world);
    release(ffi);
    release(net);
    var status = 0;
    borrow mut heap as &!h in {
        borrow args as &g in {
            borrow fs as &f in {
                borrow mut io as &!i in {
                    borrow clock as &k in {
                        status = run(h, f, g, i, k);
                    }
                }
            }
        }
    }
    release(clock);
    release(heap);
    release(args);
    release(fs);
    release(io);
    return status;
}
