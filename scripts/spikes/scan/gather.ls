edition 5;

fn gather[&t](text: &t [byte]) -> [] int {
    var s = 0;
    var i = 0;
    let n = len(text);
    while i + 16 <= n {
        let m = ((((int_of(text[i + 0]) ^ 44) - 1) >> 63) & 1) | ((((int_of(text[i + 1]) ^ 44) - 1) >> 63) & 2) | ((((int_of(text[i + 2]) ^ 44) - 1) >> 63) & 4) | ((((int_of(text[i + 3]) ^ 44) - 1) >> 63) & 8) | ((((int_of(text[i + 4]) ^ 44) - 1) >> 63) & 16) | ((((int_of(text[i + 5]) ^ 44) - 1) >> 63) & 32) | ((((int_of(text[i + 6]) ^ 44) - 1) >> 63) & 64) | ((((int_of(text[i + 7]) ^ 44) - 1) >> 63) & 128) | ((((int_of(text[i + 8]) ^ 44) - 1) >> 63) & 256) | ((((int_of(text[i + 9]) ^ 44) - 1) >> 63) & 512) | ((((int_of(text[i + 10]) ^ 44) - 1) >> 63) & 1024) | ((((int_of(text[i + 11]) ^ 44) - 1) >> 63) & 2048) | ((((int_of(text[i + 12]) ^ 44) - 1) >> 63) & 4096) | ((((int_of(text[i + 13]) ^ 44) - 1) >> 63) & 8192) | ((((int_of(text[i + 14]) ^ 44) - 1) >> 63) & 16384) | ((((int_of(text[i + 15]) ^ 44) - 1) >> 63) & 32768);
        s = wrapping_add(s, m);
        i = i + 16;
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
        z = gather(t);
    }
    release(heap);
    release(fs);
    release(args);
    release(io);
    return 0;
}
