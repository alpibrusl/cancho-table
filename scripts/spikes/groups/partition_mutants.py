#!/usr/bin/env python3
"""Mutation check of the spike (docs/gap-groups.md): each mutant is one deliberate defect in the partitioned grouping, the radix order, the
table or the row writer, rebuilt and run against fuzz.py (random files and plans; the threaded answer must equal the base binary's) and a
fixed set of full-size comparisons. A mutant is killed when the build refuses it or an answer differs.

    python3 scripts/spikes/groups/partition_mutants.py --base PATH [--check] [name-substring ...]

The sources are restored after every mutant. Do not edit tools/table while it runs; rebuild after.
"""
import os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = ROOT / "tools" / "table"
HERE = pathlib.Path(__file__).resolve().parent

M = []
def m(name, file, old, new):
    M.append((name, file, old, new))

# the partitioned read
m("the bounds are not checked on the merged totals", "par.cho", "if ng_all > bound_groups || pairs_all > bound_distinct || held_all > bound_state {", "if false {")
m("the distinct bound is not checked on the merged totals", "par.cho", "pairs_all > bound_distinct", "false")
m("a range whose guess was wrong is taken", "par.cho", "if agg.get_i64(sl, at) == 0 && first == cur {\n                            let r = agg.get_i64(sl, at + 32 + 8 * engine.k_records());\n                            let g1", "if agg.get_i64(sl, at) == 0 {\n                            let r = agg.get_i64(sl, at + 32 + 8 * engine.k_records());\n                            let g1")
m("a range that passes --max-rows is taken", "par.cho", "if before < most && before + r <= most {", "if true {")
m("the rows of a range are not counted", "par.cho", "t[engine.k_records()] = before + r;", "t[engine.k_records()] = before;")
m("a wave that makes no progress is not declined", "par.cho", "if declined == 0 && cur == wave_began {\n            declined = 1;", "if false {\n            declined = 1;")
m("an accepted range is not used", "par.cho", "                    if accepted {\n                        borrow mut used as &!uw in {\n                            contents(uw)[j] = 1;", "                    if accepted {\n                        borrow mut used as &!uw in {\n                            contents(uw)[j] = 0;")
m("every wave is the last", "par.cho", "let last = cur >= size;", "let last = true;")
m("too many tables are kept", "par.cho", "nt + nused <= 64", "nt + nused <= 100000")
m("the kept tables are not merged", "par.cho", "let nt = tabs[0];\n    var nb = nt;", "let nt = 0;\n    var nb = nt;")
m("the group count of a page is the candidates'", "par.cho", "a[engine.k_total()] = total_groups;", "a[engine.k_total()] = 0;")
m("a merge thread that did not finish is trusted", "par.cho", "if agg.get_i64(contents(s2r), at) != 0 {\n                                declined = 1;", "if false {\n                                declined = 1;")
m("the parent's own groups are not in the state", "par.cho", "borrow state as &str in {\n                acc = buffer.append(heap, acc, contents(str)[0..n0]);", "borrow state as &str in {\n                acc = buffer.append(heap, acc, contents(str)[0..0]);")
# the cut
m("a key at a splitter goes the wrong way round", "agg.cho", "contents(split)[0] + 0", "contents(split)[0] + 0") if False else None
m("the blobs are cut the wrong way", "agg.cho", "if compare_keys(key, split[at + 4..at + 4 + length_at(split, at)], fields) >= 0 {", "if compare_keys(key, split[at + 4..at + 4 + length_at(split, at)], fields) < 0 {")
m("a pair keeps the number of its group in the range", "agg.cho", "w = put_u32(out, w, work[loc + length_at(key, 0)]);", "w = put_u32(out, w, length_at(key, 0));")
m("pairs all go to blob 0", "agg.cho", "let b = work[part + length_at(key, 0)];\n        var w = work[pcur + b];", "let b = 0;\n        var w = work[pcur + b];")
m("a group's last integer is not written", "agg.cho", "            w = put_i64(out, w, vec.get(g.acc, e * g.stride + k));\n            k = k + 1;", "            w = put_i64(out, w, vec.get(g.acc, e * g.stride + k) * (k + 1 - g.stride + 1));\n            k = k + 1;")
# the order
m("the rows are not sorted", "agg.cho", "sort_keys(g, contents(ow), contents(sw), contents(ww), contents(xw), contents(hw), ng);", "{}")
m("keys that share a chunk are left as they came", "agg.cho", "if word[a] & 255 == 7 {", "if false {")
m("the second field is not looked at", "agg.cho", "} else if j + 1 < fields {", "} else if false {")
m("a field of exactly six bytes sorts with a longer one", "agg.cho", "        take = 6;\n        code = 7;", "        take = 6;\n        code = 6;")
# the table and the row
m("a group is put in place past the bound of groups", "agg.cho", "gmap.size(g.index) < limit_groups && ", "")
m("a group is put in place past the bound of bytes", "agg.cho", "g.held + more <= contents(g.memo)[size + 6] && ", "")
m("a key that needs quotes is written plain", "agg.cho", "if c == 34 || c == 13 || c == 10 || c == delim {\n                return 0 - 1;", "if false {\n                return 0 - 1;")
m("the high half of a sum is not written", "agg.cho", "v = high * 4294967296 + vec.get(g.acc, x * g.stride + 1 + k);", "v = vec.get(g.acc, x * g.stride + 1 + k);")
M[:] = [x for x in M if x is not None]

def check():
    bad = 0
    for name, file, old, new in M:
        t = (SRC / file).read_text()
        n = t.count(old)
        if n != 1:
            print("!! %s: pattern occurs %d times in %s" % (name, n, file)); bad += 1
    return bad

def main():
    argv = sys.argv[1:]
    base = None
    if "--base" in argv:
        i = argv.index("--base"); base = argv[i + 1]; del argv[i:i + 2]
    only = [a for a in argv if not a.startswith("--")]
    bad = check()
    if "--check" in argv or bad:
        print("%d of %d mutants cannot be applied" % (bad, len(M)))
        sys.exit(1 if bad else 0)
    survived = []
    for name, file, old, new in M:
        if only and not any(o in name for o in only):
            continue
        path = SRC / file
        orig = path.read_text()
        try:
            path.write_text(orig.replace(old, new, 1))
            b = subprocess.run(["cancho", "build"], cwd=ROOT, capture_output=True, text=True)
            if b.returncode:
                print("killed (does not build)  %s" % name); continue
            binpath = ROOT / "build" / "table"
            killed = False
            for seed in (101, 102):
                f = subprocess.run([sys.executable, str(HERE / "fuzz.py"), "--bin", str(binpath), "--base", base, "--cases", "60", "--seed", str(seed)], capture_output=True, text=True)
                if f.returncode:
                    killed = True; break
            print("%s  %s" % ("killed" if killed else "SURVIVED", name)); sys.stdout.flush()
            if not killed:
                survived.append(name)
        finally:
            path.write_text(orig)
    subprocess.run(["cancho", "build"], cwd=ROOT, capture_output=True)
    print("survived: %d" % len(survived))
    for s in survived:
        print("  " + s)
    sys.exit(1 if survived else 0)

main()
