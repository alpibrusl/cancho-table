edition 5;

module frame;

// What the header lets the plan become: once the header is read, every column
// the plan names is found (`plan.resolve`), and what the answer's header says
// is written, as a json array and as a csv line. For a selection the columns are
// the ones named (or all of them, for `--where` alone); for a grouping they are
// the group columns, then one per aggregate, named `count`, `sum:NAME`,
// `min:NAME`, `max:NAME`, `distinct:NAME` with the header's own spelling of NAME.

import std.buffer;
import std.bytes;
import std.json;
import std.vec;
import plan;
import query;
import toolbox.text;
import writer;

pub res struct Frame {
    fcols: Box[[int]],
    fsel: Box[[int]],
    fcells: Box[[int]],
    fhead: buffer.Buffer,
    flead: buffer.Buffer,
    flabels: buffer.Buffer,
    flends: vec.Vec[int],
    fpicked: int,
    fabort: int,
    fbad: int,
    fsort_field: int,
    fsort_slot: int,
    fdesc: int,
}

pub fn none[&h](heap: &!h Heap) -> [heap] Frame {
    return Frame { fcols: box_slice(heap, 1, 0), fsel: box_slice(heap, 1, 0), fcells: box_slice(heap, 3, 0), fhead: buffer.empty(heap, 1), flead: buffer.empty(heap, 1), flabels: buffer.empty(heap, 1), flends: vec.empty(heap, 1, 0), fpicked: 0, fabort: 0, fbad: 0, fsort_field: -1, fsort_slot: -1, fdesc: 0 };
}

pub fn drop[&h](heap: &!h Heap, f: Frame) -> [heap] int {
    let Frame { fcols, fsel, fcells, fhead, flead, flabels, flends, fpicked, fabort, fbad, fsort_field, fsort_slot, fdesc } = f;
    unbox_slice(heap, fcols);
    unbox_slice(heap, fsel);
    unbox_slice(heap, fcells);
    buffer.drop(heap, fhead);
    buffer.drop(heap, flead);
    buffer.drop(heap, flabels);
    vec.drop(heap, flends);
    return 0;
}

fn function_name(function: int) -> [] &static [byte] {
    if function == 1 {
        return "sum";
    }
    if function == 2 {
        return "min";
    }
    if function == 3 {
        return "max";
    }
    return "distinct";
}

// The header's name of column `c`.
fn header_name[&n, &e](names: &n buffer.Buffer, ends: &e vec.Vec[int], c: int) -> [] &n [byte] {
    var begin = 0;
    if c > 0 {
        begin = vec.get(ends, c - 1);
    }
    return buffer.bytes(names)[begin..vec.get(ends, c)];
}

// One name of the answer, into the json array `head` and the csv line `lead`.
fn put_label[&h, &d](heap: &!h Heap, head: buffer.Buffer, lead: buffer.Buffer, m: int, name: &d [byte], delim: int) -> [heap] (buffer.Buffer, buffer.Buffer) {
    var h2 = head;
    var l2 = lead;
    if m > 0 {
        h2 = buffer.push(heap, h2, byte_of(','));
        l2 = buffer.push(heap, l2, byte_of(delim));
    }
    h2 = text.append_json(heap, h2, name);
    l2 = writer.csv_value(heap, l2, name, delim);
    return (h2, l2);
}

// Resolve the plan against the header. `mode` is 1 for rows (a selection, a
// filter or both), 2 for a grouping. `sort` is the `--sort` value, or empty.
pub fn prepare[&h, &q, &n, &e](heap: &!h Heap, tree: &q query.Query, names: &n buffer.Buffer, ends: &e vec.Vec[int], columns: int, mode: int, delim: int, sort: &static [byte]) -> [heap] Frame {
    var cols = box_slice(heap, query.name_count(tree) + 1, 0);
    var status = 0;
    var bad = 0;
    borrow mut cols as &!cw in {
        let found = plan.resolve(heap, names, ends, tree.names, tree.nends, tree.nkinds, contents(cw));
        status = found.status;
        bad = found.at;
    }
    var head = buffer.empty(heap, 64);
    var lead = buffer.empty(heap, 64);
    var labels = buffer.empty(heap, 64);
    var lends = vec.empty(heap, 8, 0);
    var sel = box_slice(heap, 1, 0);
    var picked = 0;
    var abort = 0;
    var sort_field = -1;
    var sort_slot = -1;
    var desc = 0;
    if status != 0 {
        abort = 4 + status;
    } else if mode == 1 {
        let ns = query.count_of(tree, 0);
        picked = ns;
        if ns == 0 {
            picked = columns;
        }
        unbox_slice(heap, sel);
        sel = box_slice(heap, picked + 1, 0);
        borrow mut sel as &!sw in {
            borrow cols as &cr in {
                let s = contents(sw);
                var m = 0;
                while m < picked {
                    s[m] = m;
                    if ns > 0 {
                        s[m] = contents(cr)[m];
                    }
                    m = m + 1;
                }
            }
        }
        head = buffer.push(heap, head, byte_of('['));
        var m = 0;
        borrow sel as &sr in {
            while m < picked {
                let (h2, l2) = put_label(heap, head, lead, m, header_name(names, ends, contents(sr)[m]), delim);
                head = h2;
                lead = l2;
                m = m + 1;
            }
        }
        head = buffer.push(heap, head, byte_of(']'));
    } else {
        let ng = query.count_of(tree, 2);
        let base = query.count_of(tree, 0) + query.count_of(tree, 1);
        let na = query.agg_count(tree);
        var m = 0;
        while m < ng + na {
            borrow cols as &cr in {
                if m < ng {
                    labels = buffer.append(heap, labels, header_name(names, ends, contents(cr)[base + m]));
                } else {
                    let function = query.agg_at(tree, m - ng, 0);
                    if function == 0 {
                        labels = buffer.append(heap, labels, "count");
                    } else {
                        labels = buffer.append(heap, labels, function_name(function));
                        labels = buffer.push(heap, labels, byte_of(':'));
                        labels = buffer.append(heap, labels, header_name(names, ends, contents(cr)[query.agg_at(tree, m - ng, 1)]));
                    }
                }
            }
            var size = 0;
            borrow labels as &lr in {
                size = buffer.size(lr);
            }
            lends = vec.push(heap, lends, size);
            m = m + 1;
        }
        head = buffer.push(heap, head, byte_of('['));
        m = 0;
        var begin = 0;
        borrow labels as &lr in {
            borrow lends as &er in {
                while m < ng + na {
                    let to = vec.get(er, m);
                    let (h2, l2) = put_label(heap, head, lead, m, buffer.bytes(lr)[begin..to], delim);
                    head = h2;
                    lead = l2;
                    begin = to;
                    m = m + 1;
                }
            }
        }
        head = buffer.push(heap, head, byte_of(']'));
        if len(sort) > 0 {
            if int_of(sort[0]) == '-' {
                desc = 1;
            }
            let wanted = sort[desc..len(sort)];
            var found = -1;
            m = 0;
            begin = 0;
            borrow labels as &lr in {
                borrow lends as &er in {
                    while m < ng + na && found < 0 {
                        let to = vec.get(er, m);
                        if bytes.equal(buffer.bytes(lr)[begin..to], wanted) {
                            found = m;
                        }
                        begin = to;
                        m = m + 1;
                    }
                }
            }
            if found < 0 {
                abort = 19;
            } else if found < ng {
                sort_field = found;
            } else if query.agg_at(tree, found - ng, 0) == 0 {
                sort_slot = 0;
            } else {
                sort_slot = 1 + found - ng;
            }
        }
    }
    return Frame { fcols: cols, fsel: sel, fcells: box_slice(heap, 3 * (columns + 1), 0), fhead: head, flead: lead, flabels: labels, flends: lends, fpicked: picked, fabort: abort, fbad: bad, fsort_field: sort_field, fsort_slot: sort_slot, fdesc: desc };
}
