"""SPIKE (docs/readers.md): the design's 1,000,000-row benchmark file as JSON lines, and variants.

    jsonl_gen.py DIR [ROWS]

writes in DIR: bench.csv (scripts/bench.py's file, byte for byte), bench.jsonl (the same rows, one object per line, the
keys in the file's order: id, status, bytes, path, note; numbers as JSON numbers, text as strings), shuffled.jsonl (the key
order differs per line), nested.jsonl (the object holds a nested object and an array before the two keys read),
unicode.jsonl (every `note` has non-ASCII bytes and an escape), wide.jsonl (24 keys, the two read are the last), and
truth.json (the count of status 404 and the sum of bytes, computed from the CSV by Python's csv module)."""
import json, os, random, sys

d = sys.argv[1]
rows = int(sys.argv[2]) if len(sys.argv) > 2 else 1000000
os.makedirs(d, exist_ok=True)
random.seed(1)
data = []
for i in range(rows):
    data.append((i, random.choice([200, 200, 200, 301, 404, 500]), random.randint(0, 99999), f"/p/{random.randint(0, 999)}", f"a,b {i % 7}"))
with open(f"{d}/bench.csv", "w", newline="") as f:
    f.write("id,status,bytes,path,note\n")
    for i, s, b, p, n in data:
        f.write(f'{i},{s},{b},{p},"{n}"\n')
def dump(o):
    return json.dumps(o, separators=(",", ":"), ensure_ascii=False)
with open(f"{d}/bench.jsonl", "w") as f, open(f"{d}/shuffled.jsonl", "w") as g, open(f"{d}/nested.jsonl", "w") as h, open(f"{d}/unicode.jsonl", "w") as u, open(f"{d}/wide.jsonl", "w") as w:
    rnd = random.Random(2)
    for i, s, b, p, n in data:
        o = {"id": i, "status": s, "bytes": b, "path": p, "note": n}
        f.write(dump(o) + "\n")
        items = list(o.items())
        rnd.shuffle(items)
        g.write(dump(dict(items)) + "\n")
        h.write(dump({"id": i, "meta": {"path": p, "tags": ["x", "y", i % 3]}, "status": s, "bytes": b, "note": n}) + "\n")
        u.write(dump({"id": i, "note": f"café é\"{n}\"\\", "status": s, "bytes": b, "path": p}) + "\n")
        wide = {f"c{k:02d}": k * i for k in range(22)}
        wide["status"] = s
        wide["bytes"] = b
        w.write(dump(wide) + "\n")
import csv
count = total = 0
with open(f"{d}/bench.csv", newline="") as f:
    r = csv.reader(f)
    next(r)
    for row in r:
        if row[1] == "404":
            count += 1
        total += int(row[2])
json.dump({"count404": count, "sum_bytes": total, "rows": rows}, open(f"{d}/truth.json", "w"))
print(open(f"{d}/truth.json").read())
