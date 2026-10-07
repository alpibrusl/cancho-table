"""SPIKE (docs/readers.md section 6): make jsonx.cho, `std.json` with `parse_with`, from the compiler's own std/json.cho.

    python3 make_jsonx.py PATH/TO/cancho/std/json.cho OUT/jsonx.cho

The module is renamed `jsonx`, and `parse` is split in two: `parse_with(src, tape, st)` does what `parse` does with the two ints
of state (`st[0]` the position, `st[1]` the node count) given by the caller, and `parse` is left as it was (a region, the state, a call
to `parse_with`). This is the change proposed upstream (U1): about ten lines moved. Nothing else in the file changes."""
import sys

src = open(sys.argv[1]).read()
src = src.replace("module std.json;", "module jsonx;", 1)
a = src.index("pub fn parse[&s, &t](src: &s [byte], tape: &!t [int]) -> [] int {")
b = src.index("// ---- reading the tape")
new = '''pub fn parse[&s, &t](src: &s [byte], tape: &!t [int]) -> [] int {
    var result = 0;
    region a {
        // `st[0]` the position, `st[1]` the node count.
        let st = alloc_slice[a](2, 0);
        result = parse_with(src, tape, st);
    }
    return result;
}

// `parse` with the two ints of state given by the caller, so that no region is made per call.
pub fn parse_with[&s, &t, &q](src: &s [byte], tape: &!t [int], st: &!q [int]) -> [] int {
    var result = 0;
    st[0] = 0;
    st[1] = 0;
    skip_ws(src, st);
    var code = value_node(src, tape, st, 0);
    if code == 0 {
        skip_ws(src, st);
        if st[0] != len(src) {
            code = err_trailing();
        }
    }
    if code == 0 {
        result = st[1];
    } else {
        result = 0 - (st[0] * 16 + code);
    }
    return result;
}

'''
open(sys.argv[2], "w").write(src[:a] + new + src[b:])
