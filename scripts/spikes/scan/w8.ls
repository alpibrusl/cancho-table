edition 5;

// does a word built from 8 byte loads become one load?
fn sum8[&t](text: &t [byte]) -> [] int {
    var s = 0;
    var i = 0;
    let n = len(text);
    while i + 8 <= n {
        let w = int_of(text[i]) | (int_of(text[i + 1]) << 8) | (int_of(text[i + 2]) << 16) | (int_of(text[i + 3]) << 24) | (int_of(text[i + 4]) << 32) | (int_of(text[i + 5]) << 40) | (int_of(text[i + 6]) << 48) | (int_of(text[i + 7]) << 56);
        s = wrapping_add(s, w);
        i = i + 8;
    }
    return s;
}

fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock } = split(world);
    release(ffi);
    release(net);
    release(clock);
    var z = 0;
    region a {
        let t = alloc_slice[a](4096, byte_of(7));
        z = sum8(t);
    }
    release(heap);
    release(fs);
    release(args);
    release(io);
    return 0;
}
