#!/usr/bin/env python3
"""`read_file_opt` and `read_stream_opt`, the read loops for the reader options, are `read_file` and `read_stream` with edits, and this script writes them (docs/reader-options.md section 11).

    python3 scripts/readopt.py            # rewrite the two regions of tools/table/table.cho (after read_stream)
    python3 scripts/readopt.py --check    # fail if they are not what read_file makes (CI runs it)

Why copies and not an `if` in the loop. The read loop is the hot path of every file read and its cost is held to `main`'s by instructions retired (docs/numbers.md, G6, and the header of scripts/stream.py): an inlined
`if` in it costs a one-thread read 9 to 37 instructions a row. O1 of docs/reader-options.md first put `--skip-lines` and `--skip-prefix` into `read_file` and measured, on 100,000 rows under callgrind, +1.3 to +2.3 percent
instructions (17 to 24 a row): over the bound of 1.01. So `read_file` and `read_stream` stay what they were, and a call that gives `--skip-lines`, `--skip-prefix`, `--na`, `--decimal` or `--thousands` runs the loop made
here: the lines above the header that an option drops, and the normalizer of the cells of a row (numrow.cho) after its fields are split. `read_any` chooses.

The edits are written here as (needle, replacement) pairs that must each match once; a change to the loop is made in `read_file` and written here.
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import stream  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
TABLE = ROOT / "tools" / "table" / "table.cho"
BEGIN_OPT = "// BEGIN read_file_opt: made from read_file by scripts/readopt.py, do not edit (python3 scripts/readopt.py --check)\n"
END_OPT = "// END read_file_opt\n"
BEGIN_SOPT = "// BEGIN read_stream_opt: made from read_file_opt by scripts/readopt.py, do not edit (python3 scripts/readopt.py --check)\n"
END_SOPT = "// END read_stream_opt\n"

EDITS = [
    # the lines above the header that an option drops, and the normalizer's state
    ("    var r = lines.start(heap, cap);\n    var going = true;\n    var c = finish.Counts",
     "    var r = lines.start(heap, cap);\n    var going = true;\n"
     "    // the lines above the header that `--skip-lines` and `--skip-prefix` drop (docs/reader-options.md): looked at only before the header, so that a data line never meets them\n"
     "    let skip_n = query.skip_of(tree);\n    let skip_prefix = preamble.prefix(args, query.skipp_of(tree));\n    let skip_on = skip_n > 0 || len(skip_prefix) > 0;\n    var c = finish.Counts"),
    ("    var out_rows = buffer.empty(heap, 1);\n    var out_kept = buffer.empty(heap, 1);\n    var picked = 0;",
     "    var out_rows = buffer.empty(heap, 1);\n    var out_kept = buffer.empty(heap, 1);\n    var norm = numrow.none(heap);\n    var norm_on = false;\n    var nrow = buffer.empty(heap, 1);\n    var picked = 0;"),
    ("        let a = contents(tw);\n        while going {\n            var status = 9;",
     "        let a = contents(tw);\n        if skip_on {\n            a[engine.k_skipped()] = 1;\n        }\n        while going {\n            var status = 9;"),
    ("                    if !quoted && len(line) == 0 {\n                        // A blank line between records is not a record.",
     "                    if !quoted && len(line) == 0 && (header || number >= skip_n) {\n                        // A blank line between records is not a record."),
    ("                    } else {\n                        // The header, or the rest of a record that has quotes open: kept.",
     "                    } else if !header && !quoted && skip_on && preamble.skips(number, line, skip_n, skip_prefix) {\n                        // A line above the header that an option drops, counted.\n"
     "                        a[engine.k_skipped()] = a[engine.k_skipped()] + 1;\n                    } else {\n                        // The header, or the rest of a record that has quotes open: kept."),
    ("                                    cols = fcols;\n                                    sel = fsel;",
     "                                    cols = fcols;\n                                    // the normalizer of the cells (`--na`, `--decimal`, `--thousands`): its typed columns are the plan's, once the names are columns\n"
     "                                    numrow.drop(heap, norm);\n                                    borrow cols as &nk in {\n"
     "                                        norm = numrow.make(heap, args, tree, contents(nk), a[engine.k_columns()]);\n                                    }\n                                    borrow norm as &nz in {\n                                        norm_on = numrow.is_on(nz);\n                                    }\n                                    sel = fsel;"),
    ("    buffer.drop(heap, out_kept);\n    return (c, head, out_rows, names, hends, e);",
     "    buffer.drop(heap, out_kept);\n    numrow.drop(heap, norm);\n    buffer.drop(heap, nrow);\n    return (c, head, out_rows, names, hends, e);"),
    ("groups: a[engine.k_groups()], skipped: -1, skipped_join: -1 };", "groups: a[engine.k_groups()], skipped: a[engine.k_skipped()] - 1, skipped_join: -1 };"),
    ("groups: c.groups, skipped: -1, skipped_join: -1 };", "groups: c.groups, skipped: c.skipped, skipped_join: -1 };"),
    # a normalized row is read on one core: the ranges of a parallel read do not normalize
    ("    var threads = cli.nat(args, parsed, table, \"threads\");\n    if query.hold_of(tree) > 0 {\n        threads = 1;\n    }\n",
     "    var threads = cli.nat(args, parsed, table, \"threads\");\n    if query.hold_of(tree) > 0 || query.syn_of(tree) != 0 || query.nam_of(tree) >= 0 {\n        threads = 1;\n    }\n"),
]


def once(text, needle):
    n = text.count(needle)
    if n != 1:
        sys.exit("readopt.py: %r occurs %d times in read_file (it must occur once): the generator needs updating with the loop" % (needle[:70], n))
    return text.index(needle)


def wrap(text, marker, var):
    """The branch that begins at `marker` reads `var` (the row): the normalizer runs first and the branch reads `text`, the row or its normalized copy."""
    a = once(text, marker)
    b = text.index("{\n", a)
    depth = 0
    j = b
    while True:
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    body = text[b + 2:j]
    ind = re.match(r"\s*", body).group(0)
    body2 = re.sub(r"(?<![A-Za-z_.])%s(?![A-Za-z_0-9])" % var, "text", body)
    # the closing brace of the branch keeps its own indentation: the whitespace after the last line feed of the body
    cut = body2.rindex("\n") + 1
    tail = body2[cut:]
    body2 = body2[:cut]
    new = (ind + "var fixed = false;\n"
           + ind + "if norm_on {\n"
           + ind + "    borrow norm as &nmr in {\n"
           + ind + "        borrow mut cells as &!nw in {\n"
           + ind + "            let (nr2, changed) = numrow.apply(heap, nrow, nmr, %s, contents(nw), delim);\n" % var
           + ind + "            nrow = nr2;\n"
           + ind + "            fixed = changed;\n"
           + ind + "        }\n"
           + ind + "    }\n"
           + ind + "}\n"
           + ind + "borrow nrow as &nrr in {\n"
           + ind + "    var text = buffer.bytes(nrr);\n"
           + ind + "    if !fixed {\n"
           + ind + "        text = %s;\n" % var
           + ind + "    }\n"
           + "".join(("    " + l if l.strip() else l) for l in body2.splitlines(True))
           + ind + "}\n" + tail)
    return text[:b + 2] + new + text[j:]


def make_file_opt(source):
    i = source.index("\nfn read_file[") + 1
    j = source.index("\n}\n", i) + 3
    body = source[i:j].replace("fn read_file[", "fn read_file_opt[", 1)
    for needle, replacement in EDITS:
        once(body, needle)
        body = body.replace(needle, replacement, 1)
    body = wrap(body, "} else if engine.count_row(a, found, number, row_from) && mode != 0 {", "line")
    body = wrap(body, "} else if engine.count_row(a, found, opened, row_from) && mode != 0 {", "record")
    note = ("// The read loop of a call that gives a reader option (`--skip-lines`, `--skip-prefix`, `--na`, `--decimal`, `--thousands`): `read_file` with the lines above the header dropped and the cells of a row normalized\n"
            "// (numrow.cho). `read_any` chooses it; the plain loop above is what `main` has, because a loop is held to `main`'s instructions.\n")
    return BEGIN_OPT + note + body + "\n" + END_OPT, body


def make_stream_opt(opt_body):
    body = opt_body.replace("fn read_file_opt[", "fn read_stream_opt[", 1)
    once(body, stream.ROW)
    body = body.replace(stream.ROW, stream.ROW_STDIN, 1)
    once(body, stream.FILL)
    body = body.replace(stream.FILL, stream.FILL_STDIN, 1)
    a = once(body, stream.PAR_FROM)
    b = once(body, stream.PAR_TO)
    body = body[:a] + body[b:]
    note = ("// The read loop for standard input of a call that gives a reader option: `read_file_opt` (above) made as `read_stream` is made from `read_file`.\n")
    return BEGIN_SOPT + note + body + "\n" + END_SOPT


def main():
    source = TABLE.read_text()
    # the plain loops come first, then the two regions of this script, then nothing of ours after
    cut = source.index(BEGIN_OPT) - 1 if BEGIN_OPT in source else None
    base = source if cut is None else source[:cut] + source[source.index(END_SOPT) + len(END_SOPT):]
    region_a, opt_body = make_file_opt(base)
    region_b = make_stream_opt(opt_body)
    anchor = base.index(stream.END) + len(stream.END)
    rewritten = base[:anchor] + "\n" + region_a + "\n" + region_b + base[anchor:]
    if "--check" in sys.argv[1:]:
        if rewritten != source:
            print("readopt.py: the regions of read_file_opt and read_stream_opt in tools/table/table.cho are not what read_file makes; run python3 scripts/readopt.py")
            sys.exit(1)
        print("readopt.py: read_file_opt and read_stream_opt are what read_file makes")
        return
    TABLE.write_text(rewritten)
    print("readopt.py: wrote read_file_opt (%d lines) and read_stream_opt (%d lines)" % (region_a.count("\n"), region_b.count("\n")))


if __name__ == "__main__":
    main()
