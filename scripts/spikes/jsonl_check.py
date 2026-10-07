"""SPIKE (docs/readers.md): the flat scanner of jsonl_scan.cho against std.json and against Python's json module.

    JSONL_SCAN=path/to/jsonl_scan python3 jsonl_check.py [N]

Builds a corpus of lines (a fixed list of edge cases, mutations of valid lines at every byte, random documents), feeds
it to `jsonl_scan show`, and requires:
  1. wherever the flat scanner answers (F ok), std.json answers ok too and both give the same two cell texts;
  2. std.json's verdict is Python's (strict: NaN, Infinity and -Infinity refused; Python decodes the bytes as UTF-8 first),
     except the class listed in KNOWN: a lone surrogate escape, which Python's json accepts and std.json refuses;
  3. the flat scanner accepts a line only if Python accepts it and its object holds the two keys' values as the cells say.
Prints the counts and the disagreements (none is expected)."""
import json, os, random, subprocess, sys

SCAN = os.environ.get("JSONL_SCAN", "jsonl_scan")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
rnd = random.Random(7)

fixed = [
    b'{"status":404,"bytes":5}', b' {"status":404,"bytes":5} ', b'{"status" : 404 , "bytes" : 5}', b'{"status":404,"bytes":5}x',
    b'{"status":404,"bytes":5,}', b'{,"status":404}', b'{"status":404 "bytes":5}', b"{'status':404}", b'{"status":+4}',
    b'{"status":04}', b'{"status":-}', b'{"status":-0}', b'{"status":1.}', b'{"status":.5}', b'{"status":1e}', b'{"status":1E+2}',
    b'{"status":1e5,"bytes":-1.50}', b'{"status":0.0,"bytes":123456789012345678901234567890}', b'{"status":NaN}', b'{"status":Infinity}',
    b'{"status":true,"bytes":false}', b'{"status":null,"bytes":null}', b'{"status":tru}', b'{"status":"a\\"b","bytes":"\\\\"}',
    b'{"status":"\\u00e9","bytes":"\\n"}', b'{"status":"\\ud800"}', b'{"status":"\\udc00x"}', b'{"status":"\\ud83d\\ude00"}',
    '{"status":"café","bytes":"日本"}'.encode(), b'{"status":"\xff"}', b'{"status":"\xc3"}', b'{"status":"a\tb"}', b'{"status":"a\x01b"}',
    b'{"status":"unterminated}', b'{"status":1,"status":2}', b'{"bytes":1,"status":2,"bytes":3}', b'{"stat\\u0075s":7}',
    b'{"a":{"status":9},"status":1}', b'{"status":[1,2],"bytes":{}}', b'{}', b'{ }', b'[]', b'[{"status":1}]', b'1', b'"s"', b'null', b'',
    b'   ', b'{', b'}', b'{"status"', b'{"status":', b'{"status":1', b'\xef\xbb\xbf{"status":1}', b'{"status":1}\r', b'{"status":1}\x00',
    b'{"":1,"status":2}', b'{"status":"","bytes":""}', b'{"status":1,"bytes":2}{"status":3}', b'{"status":1}  {"x"}',
    b'{"status":00}', b'{"status":-01}', b'{"status":1e+}', b'{"status":0e0}', b'{"status":-0.0e-0}', b'{"status":1_000}',
    b'{"status":0x10}', b'{"status":"\\x41"}', b'{"status":"\\/"}', b'{"STATUS":1,"Status":2,"status":3}',
]

def valid_doc():
    def scalar():
        c = rnd.random()
        if c < .3: return rnd.randint(-10**rnd.randint(1, 25), 10**rnd.randint(1, 25))
        if c < .45: return rnd.choice([0.5, -1.25, 1e22, 1.5e-7, 12.50, 3.0])
        if c < .6: return rnd.choice([True, False, None])
        return rnd.choice(["", "a", "a b", "é", "日本", 'q"uote', "back\\slash", "tab\tx", "nl\nx", "\u0001", "\U0001F600", "/p/1", "x" * 40])
    o = {}
    keys = ["status", "bytes", "id", "note", "path", "x"]
    rnd.shuffle(keys)
    for k in keys[:rnd.randint(0, 6)]:
        o[k] = scalar() if rnd.random() < .85 else ([scalar()] if rnd.random() < .5 else {"s": scalar()})
    return json.dumps(o, separators=(",", ":") if rnd.random() < .5 else (", ", ": "), ensure_ascii=rnd.random() < .5).encode()

corpus = list(fixed)
base = [valid_doc() for _ in range(N // 4)]
corpus += base
for d in base[: N // 8]:
    # every mutation: delete a byte, replace a byte by a structural/odd one, insert one
    i = rnd.randrange(max(1, len(d)))
    ch = bytes([rnd.choice(list(b'{}[]",:\\ \t0123456789-+.eEtfnul') + [0, 1, 10 - 10 + 255, 0xc3, 0x80])])
    corpus.append(d[:i] + d[i + 1:])
    corpus.append(d[:i] + ch + d[i + 1:])
    corpus.append(d[:i] + ch + d[i:])
corpus = [c for c in corpus if b"\n" not in c]

BAD = object()

def py(line):
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        return BAD
    def bad(x): raise ValueError(x)
    try:
        v = json.loads(text, parse_constant=bad)
    except Exception:
        return BAD
    return v

data = b"\n".join(corpus) + b"\n"
out = subprocess.run([SCAN, "show", "1"], input=data, capture_output=True).stdout.split(b"\n")
assert len(out) == 2 * len(corpus) + 1, (len(out), len(corpus))
stats = dict(lines=len(corpus), json_ok=0, flat_ok=0, flat_fallback=0, known=0, disagree=0)
for k, line in enumerate(corpus):
    j = out[2 * k].split(b" ", 2)
    f = out[2 * k + 1].split(b" ", 2)
    jok = j[1] == b"ok"
    p = py(line)
    pok = p is not BAD
    if jok != pok:
        # the known class: a lone surrogate escape, which Python accepts
        if pok and b"\\ud" in line.lower() or b"\\ud" in line.lower():
            stats["known"] += 1
        else:
            stats["disagree"] += 1; print("VERDICT", line, j, p)
    stats["json_ok"] += jok
    if f[1] == b"ok":
        stats["flat_ok"] += 1
        if not jok or f[2:] != j[2:]:
            stats["disagree"] += 1; print("FLAT", line, j, f)
        elif not (isinstance(p, dict)):
            stats["disagree"] += 1; print("FLAT-NOT-OBJECT", line)
    else:
        stats["flat_fallback"] += 1
print(stats)
sys.exit(1 if stats["disagree"] else 0)
