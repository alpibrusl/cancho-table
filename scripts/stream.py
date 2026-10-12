#!/usr/bin/env python3
"""`read_stream`, the read loop for standard input, is `read_file` with three edits, and this script writes it (docs/readers.md section 3, stage R1).

    python3 scripts/stream.py            # rewrite the region of tools/table/table.cho between the BEGIN and END marks
    python3 scripts/stream.py --check    # fail if that region is not what `read_file` makes (CI runs it)
    python3 scripts/stream.py --check --file PATH   # the same for another copy of table.cho (a test's)

Why a copy and not one function with a switch. The read loop of 26 KB of machine code is the hot path of every file read, and the compiler's register allocation of it
is not stable under edits: a measured series of formulations of "fill from standard input here" (a parameter, a flag in the plan, a function call, an inlined `if`)
each cost every one-thread read 9 to 37 instructions a row on x86-64 (`scripts/gate_regress.py --counter`, 1,000,000 rows, 1.2 G instructions: 0.8 to 3 percent), and
the same loop with its text unchanged costs exactly the same as before. So `read_file` stays what it was, and the loop for a pipe is made from it mechanically:

  1. the name, and the row (it reads standard input, and starts no thread: `io_read` instead of `conc`, `fs_read` and `dir_read`);
  2. where the loop refills its chunk it fills it from standard input (`lines.fill_stdin`, one `getchar` a byte) -- and, when the line being read is already longer than
     the largest `--max-line-bytes` there is and has no end in sight, from the empty file that stands in for the input, which says the input has ended: `lines` then answers
     the line as too long, with the length so far (a producer with no newline would otherwise be read forever);
  3. the partitioned read (several threads over byte ranges) is cut out: a pipe has no ranges. `--threads` is read and not used.

Nothing else differs, which is what the check is for: a change to the loop is made in `read_file` and written here. The file loop is compared with `main` by
`scripts/corpus.py` and `scripts/gate_regress.py --counter`, and the pipe loop with the file loop by tests/conformance/test_stdin.py.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TABLE = ROOT / "tools" / "table" / "table.cho"
BEGIN = "// BEGIN read_stream: made from read_file by scripts/stream.py, do not edit (python3 scripts/stream.py --check)\n"
END = "// END read_stream\n"

FILL = "                r = lines.fill_file(r, file);\n"
FILL_STDIN = """                var length = 0;
                borrow r as &rr in {
                    length = lines.length(rr);
                }
                if length > finish.line_ceiling() {
                    r = lines.fill_file(r, file);
                } else {
                    r = lines.fill_stdin(heap, r, io);
                }
"""
PAR_FROM = "                                // The rest of the file, by several threads, when that is asked for and\n"
PAR_TO = "                            } else {\n                                // A data record that spanned lines, whole in `rec`.\n"
ROW = '-> [heap, args, file_read, io_write, conc, fs_read(""), dir_read] (finish.Counts,'
ROW_STDIN = "-> [heap, args, file_read, io_read, io_write] (finish.Counts,"


def once(text, needle):
    n = text.count(needle)
    if n != 1:
        sys.exit("stream.py: %r occurs %d times in read_file (it must occur once): the generator needs updating with the loop" % (needle[:60], n))
    return text.index(needle)


def make(source):
    i = source.index("\nfn read_file[") + 1
    j = source.index("\n}\n", i) + 3
    body = source[i:j]
    body = body.replace("fn read_file[", "fn read_stream[", 1)
    once(body, ROW)
    body = body.replace(ROW, ROW_STDIN, 1)
    once(body, FILL)
    body = body.replace(FILL, FILL_STDIN, 1)
    a = once(body, PAR_FROM)
    b = once(body, PAR_TO)
    body = body[:a] + body[b:]
    note = ("// The read loop for standard input: `read_file` (above) with the refill from `getchar`, the partitioned read cut out and the row of a pipe. Called for the input `-`\n"
            "// or none (`read_any`); `file` is the empty file that stands in for the input, and is read only to say that the input has ended.\n")
    return BEGIN + note + body + "\n" + END


def main():
    global TABLE
    if "--file" in sys.argv[1:]:
        TABLE = pathlib.Path(sys.argv[sys.argv.index("--file") + 1])
    source = TABLE.read_text()
    generated = make(source[:source.index(BEGIN)] if BEGIN in source else source)
    if BEGIN in source:
        a = source.index(BEGIN)
        b = source.index(END) + len(END)
        current = source[a:b]
        rewritten = source[:a] + generated + source[b:]
    else:
        # first time: after read_file
        i = source.index("\nfn read_file[") + 1
        j = source.index("\n}\n", i) + 3
        current = None
        rewritten = source[:j] + "\n" + generated + source[j:]
    if "--check" in sys.argv[1:]:
        if current != generated:
            print("stream.py: the region of read_stream in tools/table/table.cho is not what read_file makes; run python3 scripts/stream.py")
            sys.exit(1)
        print("stream.py: read_stream is what read_file makes")
        return
    TABLE.write_text(rewritten)
    print("stream.py: wrote read_stream (%d lines)" % generated.count("\n"))


if __name__ == "__main__":
    main()
