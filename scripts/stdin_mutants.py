#!/usr/bin/env python3
"""Mutation check of the standard-input stage (docs/readers.md, R1; tests/conformance/test_stdin.py): `table -` and `table` with no operand.

Each mutant is one defect in how the input is chosen, read, bounded or named, or in the ceiling that lets the tool read standard input; the tool is rebuilt from it
and run against the tests. A mutant is killed when the build refuses it or a test fails.

    python3 scripts/stdin_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.

A pattern in `table.cho#stream` is in the loop for standard input (the region scripts/stream.py makes from `read_file`; mutlib.py), the others are where they say.
Not here: a mutant of the guard that keeps a pipe from the partitioned read, because there is none to mutate. The loop for a pipe has no partitioned read in it (scripts/stream.py
cuts it out), so `--threads` cannot start a thread there: test_stdin.Threads counts the clone calls under strace on Linux, and scripts/stream.py --check keeps the cut.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_stdin", "test_authority", "test_inputs", "test_rules"]

MUTANTS = [
    # which input it is
    ("no operand is not standard input", "inputs.cho", "var stdin = cli.operand_count(parsed) == 0;", "var stdin = false;"),
    ("the dash is not standard input", "inputs.cho", 'bytes.equal(arg(args, source_at), "-")', 'bytes.equal(arg(args, source_at), "--")'),
    ("a file called ./- is standard input", "inputs.cho", 'bytes.equal(arg(args, source_at), "-")', 'bytes.ends_with(arg(args, source_at), "-")'),
    ("a name that starts with a dash is standard input", "inputs.cho", 'bytes.equal(arg(args, source_at), "-")', 'bytes.starts_with(arg(args, source_at), "-")'),
    ("two inputs are not refused", "table.cho", "if inp.operands > 1 {", "if inp.operands > 2 {"),
    ("the file path reads standard input", "inputs.cho", "    var stdin = cli.operand_count(parsed) == 0;", "    var stdin = true;"),
    # how it is read
    ("standard input is read and then the empty file's filler is called", "table.cho#stream", "r = bulk.fill_stdin(r, io);", "r = lines.fill_file(bulk.fill_stdin(r, io), file);"),
    ("standard input is told it is a file", "table.cho", 'opened, true, inp.jsonl, "-", "", "-", io, fs, delim', 'opened, false, inp.jsonl, "-", "", "-", io, fs, delim'),
    ("the file is told it is standard input", "table.cho", "opened, false, inp.jsonl, path.shown(tp), buffer.bytes(rr), path.full(tp), io, fs, delim", "opened, true, inp.jsonl, path.shown(tp), buffer.bytes(rr), path.full(tp), io, fs, delim"),
    ("the loop for standard input is not chosen", "table.cho", "    if stdin {\n        return read_stream(", "    if false {\n        return read_stream("),
    ("the empty file that stands in is not opened for standard input", "table.cho", 'match open_read(fs, "/dev/null") {', 'match open_read(fs, "/dev/zero") {'),
    ("an open failure of the stand-in is ignored", "table.cho", 'Opened::Failed(reason) => {\n                    e = fail.io_error(heap, e, reason, false, "-");\n                }', "Opened::Failed(reason) => {\n                }"),
    # the bulk read (tools/table/bulk.cho: read_bytes of cancho #363)
    ("the first read asks for the whole chunk", "bulk.cho", "if n < 512 {\n        n = 512;", "if n < 65536 {\n        n = 65536;"),
    ("the reads never grow", "bulk.cho", "    var n = read;\n", "    var n = 512;\n"),
    ("a read may ask for one byte more than the room", "bulk.cho", "if n > room {\n        n = room;", "if n > room + 1 {\n        n = room + 1;"),
    ("the bytes of a short read are committed one short", "bulk.cho", "buffer.filled(cw, n);", "buffer.filled(cw, n - 1);"),
    ("the errno of a failed read is lost", "bulk.cho", "                bad = reason;\n", "                bad = 0;\n"),
    ("a failed read is the end of the input", "bulk.cho", "Read::Failed(reason) => {\n                e = true;\n                bad = reason;\n                if bad == 0 {\n                    bad = 5;\n                }\n            }", "Read::Failed(reason) => {\n                e = true;\n            }"),
    # (Not here: "the refill keeps the view of the last chunk", `viewing: viewing, ...` for `viewing: false, ...`. It survives and is equivalent: the refill is only called
    # after `lines.next` answered `need`, and `lines.next` starts every answer with the view off, so the fields it would keep are already false and 0.)
    # how it is named
    ("a refusal names standard input as stdin", "table.cho", 'opened, true, inp.jsonl, "-", "", "-", io, fs, delim', 'opened, true, inp.jsonl, "stdin", "", "-", io, fs, delim'),
    ("a refusal names no path", "table.cho", 'opened, true, inp.jsonl, "-", "", "-", io, fs, delim', 'opened, true, inp.jsonl, "", "", "-", io, fs, delim'),
    # how it is bounded
    ("a line with no end is read to its end", "table.cho#stream", "if length > finish.line_ceiling() {", "if length > 1000000000000 {"),
    ("a line with no end is cut at the cap, not the ceiling", "table.cho#stream", "if length > finish.line_ceiling() {", "if length > 1000 {"),
    # the ceiling that lets the tool read it (a mutant of tools.toml, not of the source): the tests that read it must notice
    ("the ceiling forgets io_read", "../../tools.toml", '"heap", "io_read", "io_write"]', '"heap", "io_write"]'),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
