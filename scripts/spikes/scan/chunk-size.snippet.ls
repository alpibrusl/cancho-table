// the spike that varied the read chunk (docs/gap-scan.md section 7); lines.Lines is the contract's reader
// The same reader with a chunk of `n` bytes (nothing has been read yet).
fn bigger[&h](heap: &!h Heap, r: lines.Lines, n: int) -> [heap] lines.Lines {
    let lines.Lines { chunk, pos, held, cap, seen, begin, count, over, ended, newline, ready, read, nul, failed, viewing, view_from, view_to } = r;
    buffer.drop(heap, chunk);
    return lines.Lines { chunk: buffer.empty(heap, n), pos: pos, held: held, cap: cap, seen: seen, begin: begin, count: count, over: over, ended: ended, newline: newline, ready: ready, read: read, nul: nul, failed: failed, viewing: viewing, view_from: view_from, view_to: view_to };
}

// The read chunk of a range (spike: the contract's is 65536).
fn chunk_n() -> [] int {
    return 131072;
}

// and in scan_range after lines.start: if chunk_n() != 65536 { r = bigger(heap, r, chunk_n()); }
