#!/usr/bin/env python3
"""Generates docs/stories.html and docs/stories/*: eleven end-to-end stories of increasing complexity, from a first look at an order export to the same bytes on four cores, then four short ones from the sciences. Every
command is RUN against build/table on the data of docs/stories/ and every number, excerpt and figure is drawn from what it printed; the data are invented (seeded generators below), the runs are real, and the narration is checked
against the numbers: a sentence that stops being true stops the build of the page.

    python3 scripts/gen_stories.py [--bin build/table] [--check]

`--check` regenerates in memory and fails if docs/stories.html or the data files differ (the answers are the same bytes everywhere). scripts/storylib.py is the same file as in cancho-stats and cancho-ml."""
import argparse
import datetime
import hashlib
import json
import math
import pathlib
import random
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import tempfile
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from storylib import W, STORY_CSS, base_css, claim, pretty, num, esc, axis, svg, wrap_cmd, term, story, fig_hashes  # noqa: E402

DOCS = ROOT / "docs"


# ------------------------------------------------------------------------------------------------ the invented data
def q(s):
    s = str(s)
    return '"%s"' % s.replace('"', '""') if ("," in s or '"' in s) else s


def csv_of(header, rows):
    return ",".join(header) + "\n" + "\n".join(",".join(q(x) for x in row) for row in rows) + "\n"


def make_shop():
    """240 orders of a ceramics shop over six months, 16 customers in five countries"""
    r = random.Random(31)
    items = [("mug", 14.50), ("plate", 11.90), ("bowl", 9.75), ("teapot", 32.00), ("vase", 44.90), ("cup set", 27.45)]
    countries = ["ES", "FR", "DE", "PT", "IT"]
    names = ["Ana", "Bruno", "Carla", "Dani", "Elena", "Fran", "Gala", "Hugo", "Ines", "Jon", "Kira", "Leo", "Mara", "Nico", "Olga", "Pau"]
    customers = [(i, n, r.choice(countries)) for i, n in enumerate(names, 1)]
    notes = ["", "", "", "gift, wrap please", 'for "Mama"', "", "leave at door"]
    orders = []
    for k in range(1, 241):
        cid = r.randint(1, 16)
        it, p = r.choice(items)
        qty = r.choice([1, 1, 1, 2, 3])
        m, d = r.choice([1, 2, 3, 4, 5, 6]), r.randint(1, 28)
        st = r.choices(["paid", "refunded", "cancelled"], [0.88, 0.06, 0.06])[0]
        price = round(p * (1 + r.choice([0, 0, 0.1, -0.1])), 2)
        orders.append((k, cid, "2026-%02d-%02d" % (m, d), it, qty, "%.2f" % price, st, r.choice(notes)))
    return customers, orders


def make_plate():
    """a 96-well growth plate: three compounds at eight concentrations in quadruplicate, optical density after 18 hours; blanks in the last row"""
    r = random.Random(14)
    rows = []
    concs = ["0.01", "0.03", "0.10", "0.30", "1.00", "3.00", "10.00", "30.00"]
    ic50 = {"A": 0.8, "B": 4.0, "C": 60.0}
    for comp in "ABC":
        for c in concs:
            for rep in range(4):
                od = 0.12 + 0.95 / (1 + (float(c) / ic50[comp]) ** 1.3) + r.gauss(0, 0.015)
                rows.append((comp, c, "%.3f" % od))
    for _ in range(8):
        rows.append(("blank", "0.00", "%.3f" % (0.04 + r.gauss(0, 0.003))))
    return rows


def make_titration():
    """burette readings (mL) of 24 titrations by three analysts; one analyst writes a third decimal"""
    r = random.Random(6)
    rows = []
    for k in range(24):
        analyst = ("Ines", "Marc", "Sara")[k % 3]
        v = round(24.35 + {"Ines": 0.0, "Marc": 0.12, "Sara": -0.05}[analyst] + r.gauss(0, 0.06), 2)
        rows.append((k + 1, analyst, "%.2f" % v))
    return rows


def make_sensor():
    """a temperature logger: a reading every five minutes for two days, as JSON lines"""
    r = random.Random(12)
    t0 = datetime.datetime(2026, 3, 5)
    out = []
    for i in range(576):
        ts = t0 + datetime.timedelta(minutes=5 * i)
        temp = 15 + 6 * math.sin(i / 288 * 2 * math.pi - 1.3) + r.gauss(0, 0.4)
        out.append(json.dumps({"ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "sensor": "A", "temp_c": round(temp, 2)}))
    return out


def make_survey():
    """400 answers to one question in four districts; some people answered twice (the same respondent id)"""
    r = random.Random(77)
    rows = []
    for k in range(400):
        d = r.choice(["Centro", "Norte", "Sur", "Este"])
        age = r.randint(15, 80)
        p = {"Centro": (0.5, 0.3), "Norte": (0.35, 0.4), "Sur": (0.65, 0.2), "Este": (0.45, 0.35)}[d]
        x = r.random()
        a = "yes" if x < p[0] else ("no" if x < p[0] + p[1] else "unsure")
        rows.append(("R%03d" % (r.randint(1, 380) if k >= 380 else k + 1), d, age, a))
    return rows


# ------------------------------------------------------------------------------------------------ real runs
class Runner:
    def __init__(self, binary, folder):
        self.binary = binary
        self.folder = folder

    def raw(self, args, file):
        return subprocess.run([self.binary, "--root", str(self.folder)] + args + [file], capture_output=True, text=True, timeout=600)

    def run(self, args, file):
        p = self.raw(args, file)
        if p.returncode != 0:
            raise SystemExit("gen_stories: `%s` exited %d: %s" % (" ".join(args), p.returncode, (p.stdout + p.stderr)[:300]))
        return p.stdout

    def json(self, args, file):
        return json.loads(self.run(args, file))["data"]

    def csv(self, args, file):
        lines = self.run(args + ["--format", "csv"], file).strip().split("\n")
        head = lines[0].split(",")
        return head, [ln.split(",") for ln in lines[1:]]

    def refusal(self, args, file):
        p = self.raw(args, file)
        if p.returncode == 0:
            raise SystemExit("gen_stories: `%s` was expected to be refused" % " ".join(args))
        return p.returncode, json.loads(p.stdout)["error"]


def make_campaign():
    """the autumn campaign: 8000 users (arm assigned by a coin, half held out) and their purchases around the start date; 6.2% of the shown and 5.0% of the held-out buy in the campaign window, a few of each bought in August too"""
    r = random.Random(11)
    users, purchases = [], []
    pid = 0
    for u in range(1, 8001):
        arm = "ads" if u % 2 == 0 else "holdout"
        users.append((u, arm))
        if r.random() < (0.062 if arm == "ads" else 0.05):
            for _ in range(1 + (r.random() < 0.15)):
                pid += 1
                purchases.append((pid, u, "2026-09-%02d" % r.randint(1, 28), "%.2f" % r.lognormvariate(3.7, 0.6)))
        if r.random() < 0.03:
            pid += 1
            purchases.append((pid, u, "2026-08-%02d" % r.randint(1, 31), "%.2f" % r.lognormvariate(3.7, 0.6)))
    purchases.sort(key=lambda p: (p[2], p[0]))
    return users, [(i,) + p[1:] for i, p in enumerate(purchases, 1)]


def make_channels():
    """eight weeks of spend per channel (euros and cents) and 640 orders credited, by last click, to a channel"""
    r = random.Random(41)
    base = {"search": 900, "social": 1400, "email": 150, "display": 1100}
    spend = [(w, c, "%.2f" % (b * r.uniform(0.85, 1.15))) for w in range(1, 9) for c, b in base.items()]
    weights = {"search": 0.5, "social": 0.25, "email": 0.15, "display": 0.1}
    mean = {"search": 52, "social": 48, "email": 61, "display": 40}
    sales = []
    for k in range(1, 641):
        c = r.choices(list(weights), list(weights.values()))[0]
        sales.append((k, c, "%.2f" % max(5, r.gauss(mean[c], 12))))
    return spend, sales


def shown(args, file):
    import re
    return "table " + " ".join(a if re.fullmatch(r"[\w.,:=/@+-]+", a) else "'%s'" % a for a in args) + " " + file


def tidy(err, keys):
    """the refusal as the tool printed it, with the program and the --root of this run taken out of the repair (they are paths of the machine that generated the page)"""
    out = {k: err[k] for k in keys if k in err}
    rep = json.loads(json.dumps(err["repair"]))
    def fix(argv):
        res, skip = ["table"], False
        for a in argv[1:]:
            if skip:
                skip = False
            elif a == "--root":
                skip = True
            else:
                res.append(a)
        return res
    if rep["kind"] == "choose":
        for o in rep["options"]:
            o["argv"] = fix(o["argv"])
    elif "argv" in rep:
        rep["argv"] = fix(rep["argv"])
    out["repair"] = rep
    return out


def repair_args(err, file):
    """the repair the tool offered, as the arguments after `table --root DIR`"""
    argv = err["repair"]["options"][0]["argv"] if err["repair"]["kind"] == "choose" else err["repair"]["argv"]
    argv = [a for a in argv[1:]]
    # drop --root DIR and the file
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == "--root":
            skip = True
            continue
        out.append(a)
    assert out[-1] == file, out
    return out[:-1]


# ------------------------------------------------------------------------------------------------ figures
def fig_table(header, rows, hot=None):
    """the first rows of a file as a grid; `hot` is (row, column) of a cell to mark"""
    cw = [60, 66, 84, 64, 40, 54, 70, 150]
    x0 = 20
    body = []
    y = 24
    x = x0
    for h, w in zip(header, cw):
        body.append('<text class="nm" x="%g" y="%g">%s</text>' % (x, y, esc(h)))
        x += w
    body.append('<line class="ax" x1="%g" y1="32" x2="%g" y2="32"/>' % (x0 - 6, sum(cw) + x0))
    for i, row in enumerate(rows):
        y = 56 + i * 24
        x = x0
        for j, (cell, w) in enumerate(zip(row, cw)):
            if hot == (i, j):
                body.append('<rect class="hot" x="%g" y="%g" width="%g" height="20" rx="3"/>' % (x - 4, y - 15, w - 4))
            body.append('<text class="cell" x="%g" y="%g">%s</text>' % (x, y, esc(cell)))
            x += w
    return svg(56 + len(rows) * 24 + 8, "\n".join(body), "The first rows of the order file")


def fig_hbars(items, unit="", fmt="%.2f", label_w=110):
    mx = max(v for _, v in items)
    body = []
    for k, (name, v) in enumerate(items):
        y = 26 + k * 34
        w = (W - label_w - 120) * v / mx
        body.append('<text class="nm" x="%g" y="%g" text-anchor="end">%s</text><rect class="pbar pa" x="%g" y="%g" width="%g" height="20" rx="3"/><text class="tk" x="%g" y="%g">%s%s</text>' % (label_w - 8, y + 5, esc(name), label_w, y - 10, w, label_w + w + 8, y + 5, esc(fmt % v), esc(unit)))
    return svg(26 + 34 * len(items) + 6, "\n".join(body), "Bars")


def fig_vbars(items, ylabel, fmt="%.0f"):
    mx = max(v for _, v in items)
    n = len(items)
    bw = 70
    gap = (W - 120 - n * bw) / max(n - 1, 1)
    body = ['<line class="ax" x1="60" y1="230" x2="%g" y2="230"/>' % (W - 40)]
    for k, (name, v) in enumerate(items):
        x = 70 + k * (bw + gap)
        h = 190 * v / mx
        body.append('<rect class="pbar pa" x="%g" y="%g" width="%g" height="%g" rx="3"/><text class="tk" x="%g" y="%g" text-anchor="middle">%s</text><text class="tk" x="%g" y="%g" text-anchor="middle">%s</text>' % (x, 230 - h, bw, h, x + bw / 2, 224 - h, esc(fmt % v), x + bw / 2, 248, esc(name)))
    body.append('<text class="lb" x="%g" y="272" text-anchor="middle">%s</text>' % (W / 2, esc(ylabel)))
    return svg(284, "\n".join(body), ylabel)


def fig_flow(steps):
    """boxes joined by arrows: (title, subtitle, class)"""
    n = len(steps)
    bw = (W - 40 - (n - 1) * 34) / n
    body = ['<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="context-stroke"/></marker></defs>']
    for k, (title, sub, cls) in enumerate(steps):
        x = 20 + k * (bw + 34)
        body.append('<rect class="fbox %s" x="%g" y="20" width="%g" height="92" rx="10"/>' % (cls, x, bw))
        body.append('<text class="nm" x="%g" y="48" text-anchor="middle">%s</text>' % (x + bw / 2, esc(title)))
        for i, line in enumerate(sub):
            body.append('<text class="tk" x="%g" y="%g" text-anchor="middle">%s</text>' % (x + bw / 2, 70 + i * 16, esc(line)))
        if k < n - 1:
            body.append('<line class="arrow" x1="%g" y1="66" x2="%g" y2="66"/>' % (x + bw + 4, x + bw + 30))
    return svg(132, "\n".join(body), "A question, its refusal and the repair")


def fig_lines(series, xs, xlabel, ylabel):
    """lines on a logarithmic x axis"""
    lo, hi = math.log10(0.005), math.log10(60)
    ax, sx = axis(70, 600, 250, lo, hi, [math.log10(v) for v in (0.01, 0.1, 1, 10)], xlabel, fmt=lambda v: "%g" % (10 ** v))
    sy = lambda v: 230 - v / 1.2 * 200
    body = [ax, '<line class="ax" x1="70" y1="30" x2="70" y2="250"/>']
    for t in (0, 0.4, 0.8, 1.2):
        body.append('<text class="tk" x="62" y="%g" text-anchor="end">%g</text>' % (sy(t) + 4, t))
    body.append('<text class="lb" x="16" y="140" transform="rotate(-90 16 140)" text-anchor="middle">%s</text>' % esc(ylabel))
    ends = sorted(range(len(series)), key=lambda i: sy(series[i][1][-1]))
    label_y = {}
    last = -99
    for i in ends:
        y = max(sy(series[i][1][-1]) + 4, last + 15)
        label_y[i] = y
        last = y
    for k, (name, ys, cls) in enumerate(series):
        pts = " ".join("%g,%g" % (sx(math.log10(x)), sy(y)) for x, y in zip(xs, ys))
        body.append('<polyline class="ln %s" points="%s"/>' % (cls, pts))
        for x, y in zip(xs, ys):
            body.append('<circle class="dt %s" cx="%g" cy="%g" r="3.5"/>' % (cls, sx(math.log10(x)), sy(y)))
        body.append('<text class="nm" x="%g" y="%g" style="fill:var(--%s)">%s</text>' % (606, label_y[k], "accent" if cls == "la" else ("good" if cls == "lb" else "warn"), esc(name)))
    return svg(300, "\n".join(body), "Mean optical density against concentration")


def fig_strip(groups, lo, hi, ticks, label):
    ax, sx = axis(100, 610, 40 + 52 * len(groups), lo, hi, ticks, label, fmt=lambda v: "%g" % v)
    body = [ax]
    for k, (name, vs, mean) in enumerate(groups):
        y0 = 36 + k * 52
        body.append('<text class="nm" x="92" y="%g" text-anchor="end">%s</text><line class="band" x1="100" y1="%g" x2="610" y2="%g"/>' % (y0 + 5, esc(name), y0, y0))
        for i, v in enumerate(sorted(vs)):
            body.append('<circle class="dot pa" cx="%g" cy="%g" r="4"/>' % (sx(v), y0 + ((i * 7919) % 5 - 2) * 3.5))
        body.append('<line class="md" x1="%g" y1="%g" x2="%g" y2="%g"/>' % (sx(mean), y0 - 16, sx(mean), y0 + 16))
    return svg(40 + 52 * len(groups) + 40, "\n".join(body), "Every reading, by analyst, with the mean")


def fig_band(hours, lo, hi):
    """per hour: the minimum to the maximum as a band and the mean as a line"""
    n = len(hours)
    ax, sx = axis(70, 610, 230, 0, n - 1, [0, 12, 24, 36, 47], "hours since midnight, 5 March", fmt=lambda v: "%d" % v)
    sy = lambda v: 210 - (v - lo) / (hi - lo) * 180
    top = " ".join("%g,%g" % (sx(i), sy(h[2])) for i, h in enumerate(hours))
    bot = " ".join("%g,%g" % (sx(i), sy(h[1])) for i, h in reversed(list(enumerate(hours))))
    mean = " ".join("%g,%g" % (sx(i), sy(h[3])) for i, h in enumerate(hours))
    body = [ax, '<line class="ax" x1="70" y1="20" x2="70" y2="230"/>', '<polygon class="bandci" points="%s %s"/>' % (top, bot), '<polyline class="ln la" points="%s"/>' % mean]
    for t in (10, 15, 20, 25):
        body.append('<text class="tk" x="62" y="%g" text-anchor="end">%d</text>' % (sy(t) + 4, t))
    body.append('<text class="lb" x="16" y="125" transform="rotate(-90 16 125)" text-anchor="middle">temperature, &#176;C</text>')
    body.append('<text class="tk" x="610" y="14" text-anchor="end">band: the hour&#8217;s minimum to maximum &#183; line: its mean</text>')
    return svg(280, "\n".join(body), "Temperature by hour: minimum, maximum and mean")


def fig_stack(groups):
    colors = {"yes": "lk5", "unsure": "lk3", "no": "lk1"}
    body = []
    for k, (name, counts) in enumerate(groups):
        y = 40 + k * 44
        tot = sum(counts.values())
        x = 110
        body.append('<text class="nm" x="102" y="%g" text-anchor="end">%s</text>' % (y + 5, esc(name)))
        for a in ("yes", "unsure", "no"):
            w = 480 * counts[a] / tot
            body.append('<rect class="%s" x="%g" y="%g" width="%g" height="24"/><text class="lk" x="%g" y="%g" text-anchor="middle">%d</text>' % (colors[a], x, y - 8, w, x + w / 2, y + 8, counts[a]))
            x += w
    body.append('<text class="tk" x="110" y="18">yes</text><text class="tk" x="350" y="18" text-anchor="middle">unsure</text><text class="tk" x="590" y="18" text-anchor="end">no</text>')
    return svg(40 + 44 * len(groups) + 8, "\n".join(body), "Answers by district")


EXTRA_CSS = """
.fig .cell { fill: var(--fg); font: 12px var(--mono); } .fig .hot { fill: var(--warn); opacity: .22; }
.fig .pbar.pa { fill: var(--accent); }
.fig .fbox { fill: var(--surface); stroke-width: 2; } .fig .fbox.q { stroke: var(--accent); } .fig .fbox.r { stroke: var(--bad); } .fig .fbox.ok { stroke: var(--good); }
.fig .arrow { stroke: var(--muted); stroke-width: 1.8; marker-end: url(#ar); }
.fig .ln { fill: none; stroke-width: 2.5; } .fig .ln.la { stroke: var(--accent); } .fig .ln.lb { stroke: var(--good); } .fig .ln.lc { stroke: var(--warn); }
.fig .dt.la { fill: var(--accent); } .fig .dt.lb { fill: var(--good); } .fig .dt.lc { fill: var(--warn); }
.fig .bandci { fill: var(--accent); opacity: .18; }
.fig .md { stroke: var(--fg); stroke-width: 3; }
.fig .dot.pa { fill: var(--accent); opacity: .8; } .fig .band { stroke: var(--line); stroke-width: 1; }
.fig .digits { fill: var(--fg); font: 600 22px var(--mono); }
.hdr { padding: 0; }
"""


def digits_fig(rows):
    """several answers to the same sum, one under the other"""
    body = []
    for k, (label, text, cls) in enumerate(rows):
        y = 34 + k * 46
        body.append('<text class="nm" x="210" y="%g" text-anchor="end">%s</text><text class="digits" x="226" y="%g" style="fill:var(--%s)">%s</text>' % (y, esc(label), y, cls, esc(text)))
    return svg(34 + 46 * len(rows), "\n".join(body), "The same sum as different programs print it")


# ------------------------------------------------------------------------------------------------ the page
def build(binary):
    customers, orders = make_shop()
    files = {
        "orders.csv": csv_of(["order_id", "customer_id", "date", "item", "qty", "price", "status", "note"], orders),
        "customers.csv": csv_of(["customer_id", "name", "country"], customers),
        "plate.csv": csv_of(["compound", "conc_uM", "od600"], make_plate()),
        "titration.csv": csv_of(["run", "analyst", "volume_ml"], make_titration()),
        "sensor.jsonl": "\n".join(make_sensor()) + "\n",
        "survey.csv": csv_of(["respondent", "district", "age", "answer"], make_survey()),
    }
    with tempfile.TemporaryDirectory() as t:
        folder = pathlib.Path(t)
        for name, text in files.items():
            (folder / name).write_text(text)
        R = Runner(binary, folder)
        S = []
        # 1 ------------------------------------------------------------------------------------------------
        a1 = []
        d1 = R.json(a1, "orders.csv")
        a1b = ["--select", "order_id,note", "--limit", "4", "--format", "csv"]
        o1b = R.run(a1b, "orders.csv")
        claim(d1["row_count"] == 240 and d1["column_count"] == 8, "story 1: 240 orders and 8 columns")
        quoted = [r for r in orders if "," in r[7]]
        claim(quoted, "story 1: some note has a comma")
        first = [[str(x) for x in r] for r in orders[:5]]
        S.append(story(1, "What is in the file?", "start here", ["table"],
            "&#8220;I sell ceramics online. The shop exports every order as a CSV. Before I ask anything, what did it give me?&#8221; &#8212; Luc&#237;a",
            ["<p>With no flags, <code>table</code> only reads the file: how many columns and rows, and what the header is. Nothing is guessed about types; every column is text until you say otherwise. The same call selects columns and prints CSV that another program can read, and a comma inside a quoted field stays inside it.</p>",
             term(shown(a1, "orders.csv"), pretty(d1)),
             term(shown(a1b, "orders.csv"), o1b.rstrip("\n")),
             fig_table(["order_id", "customer_id", "date", "item", "qty", "price", "status", "note"], first),
             '<div class="read"><b>Reading it.</b> %d orders, %d columns. The first rows above are what the file holds; the notes with a comma (&#8220;gift, wrap please&#8221;) are one cell each, not two. The rest of the page is questions asked of this one file, each a little harder than the one before.</div>' % (d1["row_count"], d1["column_count"])]))
        # 2 ------------------------------------------------------------------------------------------------
        a2 = ["--where", "status = paid and price:dec(2) >= 20.00", "--group", "item", "--agg", "count,sum:price:dec(2)", "--sort", "-sum:price"]
        head, rows2 = R.csv(a2, "orders.csv")
        o2 = R.run(a2 + ["--format", "csv"], "orders.csv")
        exp = {}
        for r in orders:
            if r[6] == "paid" and Decimal(r[5]) >= Decimal("20.00"):
                c, s_ = exp.get(r[3], (0, Decimal(0)))
                exp[r[3]] = (c + 1, s_ + Decimal(r[5]))
        claim({r[0]: (int(r[1]), Decimal(r[2])) for r in rows2} == exp, "story 2: the groups are what an exact recount of the file gives")
        S.append(story(2, "One question, exactly", "a filter and a group", ["--where", "--group", "--agg", "--sort"],
            "&#8220;Which of the pieces that sell for twenty euros or more earned the most from orders that were actually paid?&#8221;",
            ["<p>One plan, read left to right: keep the paid orders priced at 20.00 or more, group by item, count them and add their prices, sort by that sum. <code>price:dec(2)</code> says the column holds exact decimals with two fractional digits; a bare <code>status = paid</code> compares bytes.</p>",
             term(shown(a2 + ["--format", "csv"], "orders.csv"), o2.rstrip("\n")),
             fig_hbars([(r[0], float(r[2])) for r in rows2], " &#8364;"),
             '<div class="read"><b>Reading it.</b> %s leads with %s euros from %s paid orders. The numbers are the sums of exact decimals, checked here against a recount of the file in Python&#8217;s <code>decimal</code> when the page is generated.</div>' % (rows2[0][0], rows2[0][2], rows2[0][1])]))
        # 3 ------------------------------------------------------------------------------------------------
        mill = folder / "million.csv"
        mill.write_text("x\n" + "0.1\n" * 1000000)
        ta = ["--agg", "sum:x:dec(1)"]
        tb = ["--agg", "sum:x:float"]
        e3a = R.json(ta, "million.csv")["rows"][0][0]
        e3b = R.json(tb, "million.csv")["rows"][0][0]
        py_naive = 0.0
        for _ in range(1000000):
            py_naive += 0.1
        claim(e3a == "100000.0" and e3b == "100000.0" and py_naive != 100000.0, "story 3: a million tenths is exactly 100000.0 for both sums of table and not for a loop of doubles")
        a3a = ["--agg", "sum:price:dec(2)"]
        d3a = R.json(a3a, "orders.csv")
        exact_total = sum(Decimal(r[5]) for r in orders)
        claim(d3a["rows"][0][0] == str(exact_total), "story 3: the total is the exact sum")
        S.append(story(3, "Why decimals? A million tenths", "exact arithmetic", ["sum:COL:dec(S)", "sum:COL:float"],
            "&#8220;My spreadsheet says the total is a few millionths off. It cannot be, these are euros and cents.&#8221;",
            ["<p>A double cannot hold a tenth, so a program that adds tenths as doubles accumulates the error. <code>table</code> lets you choose: <code>:dec(S)</code> is an exact decimal with S fractional digits (a cell with more digits is <em>refused</em>, never rounded), <code>:float</code> is the nearest double. A file of one million cells, each <code>0.1</code> (generated by this page&#8217;s script), summed both ways, and the loop a script would write:</p>",
             term(shown(ta, "million.csv"), R.run(ta, "million.csv").rstrip("\n")),
             term(shown(tb, "million.csv"), R.run(tb, "million.csv").rstrip("\n")),
             digits_fig([("exact decimal", e3a, "good"), ("float sum in table", e3b, "accent"), ("a loop of doubles", repr(py_naive), "bad")]),
             "<p>And the real column: the price of every order, added exactly.</p>",
             term(shown(a3a, "orders.csv"), pretty(d3a["rows"])),
             '<div class="read"><b>Reading it.</b> A million tenths is 100000.0. The decimal sum says so exactly; the float sum of <code>table</code> also gives %s here, and the loop of a million additions of 0.1 in doubles gives %s: the error is %s, and it grows with the number of rows. (A float sum is still a double, so nothing promises that it is exact in general; a declared <code>:dec</code> is the way to add money.) The sum of the %d prices of the orders is %s euros, exact, with no rounding step anywhere.</div>' % (e3b, repr(py_naive), "{:.1e}".format(py_naive - 100000.0), len(orders), d3a["rows"][0][0])]))
        # 4 ------------------------------------------------------------------------------------------------
        a4 = ["--where", "status = paid", "--select", "Item"]
        rc4, err4 = R.refusal(a4, "orders.csv")
        fix4 = repair_args(err4, "orders.csv")
        o4 = R.run(fix4 + ["--limit", "3", "--format", "csv"], "orders.csv")
        a4b = ["--where", "price:dec(1) >= 20"]
        rc4b, err4b = R.refusal(a4b, "orders.csv")
        fix4b = repair_args(err4b, "orders.csv")
        o4b = R.run(fix4b + ["--limit", "2", "--format", "csv"], "orders.csv")
        claim(err4["rule"] == "select.unknown-column" and err4b["rule"] == "value.decimal-scale" and fix4[-1] == "item" and "price:dec(2) >= 20" in fix4b, "story 4: the rules and both repairs are what the narration says")
        S.append(story(4, "A refusal that tells you what to type", "errors as data", ["select.unknown-column", "value.decimal-scale"],
            "&#8220;I asked for the wrong column. And then I declared the price with one decimal. What happens?&#8221;",
            ["<p>A wrong request is not guessed at. <code>table</code> exits with a code, prints one JSON line naming the <em>rule</em> that was broken, a hint in a sentence and a <em>repair</em>: the corrected command as arguments, ready to run. First the misspelt column:</p>",
             term(shown(a4, "orders.csv"), "exit %d\n%s" % (rc4, pretty(tidy(err4, ("code", "rule", "message", "hint"))))),
             term(shown(fix4 + ["--limit", "3", "--format", "csv"], "orders.csv"), o4.rstrip("\n")),
             "<p>Then a price declared with too few decimals: the data have two, and a decimal is never rounded to fit.</p>",
             term(shown(a4b, "orders.csv"), "exit %d\n%s" % (rc4b, pretty(tidy(err4b, ("code", "rule", "message", "hint"))))),
             term(shown(fix4b + ["--limit", "2", "--format", "csv"], "orders.csv"), o4b.rstrip("\n")),
             fig_flow([("the request", ["--select Item"], "q"), ("the refusal", [err4["rule"].split(".")[0] + ".", err4["rule"].split(".")[1], "exit %d" % rc4], "r"), ("the repair", ["--select item"], "ok"), ("the answer", ["3 rows"], "ok")]),
             '<div class="read"><b>Reading it.</b> Both refusals have a stable <em>rule</em> (<code>%s</code>, <code>%s</code>) that a program can match on, and each carries its own fix: the corrected command, which is the one that was then run. The second repair is the same plan with <code>:dec(2)</code>, the most digits the column holds. A person reads the hint; an agent runs the repair.</div>' % (err4["rule"], err4b["rule"])]))
        # 5 ------------------------------------------------------------------------------------------------
        a5 = ["--where", "status = paid", "--group", "date:date@month", "--agg", "count,sum:price:dec(2)", "--sort", "date"]
        o5 = R.run(a5 + ["--format", "csv"], "orders.csv")
        head5, rows5 = R.csv(a5, "orders.csv")
        claim(len(rows5) == 6 and sum(int(r[1]) for r in rows5) == sum(1 for r in orders if r[6] == "paid"), "story 5: six months, every paid order counted once")
        best = max(rows5, key=lambda r: Decimal(r[2]))
        S.append(story(5, "By month", "dates as keys", ["--group date:date@month"],
            "&#8220;How did the months compare?&#8221;",
            ["<p>A column read as <code>:date</code> can be a group key at a coarser unit: <code>date:date@month</code> puts every order of a month in one group. The column is checked as a date (a cell like <code>2026-02-30</code> is refused, with its line), not trusted as text.</p>",
             term(shown(a5 + ["--format", "csv"], "orders.csv"), o5.rstrip("\n")),
             fig_vbars([(r[0][:7], float(r[2])) for r in rows5], "paid revenue by month, euros"),
             '<div class="read"><b>Reading it.</b> %s was the strongest month, with %s euros from %s paid orders. The six counts add up to the %d paid orders of the file, which is checked when this page is generated.</div>' % (best[0][:7], best[2], best[1], sum(1 for r in orders if r[6] == "paid"))]))
        # 6 ------------------------------------------------------------------------------------------------
        a6 = ["--join", "customers.csv", "--on", "customer_id", "--where", "status = paid", "--group", "country", "--agg", "count,sum:price:dec(2)", "--sort", "-sum:price"]
        o6 = R.run(a6 + ["--format", "csv"], "orders.csv")
        head6, rows6 = R.csv(a6, "orders.csv")
        cc = {c[0]: c[2] for c in customers}
        exp6 = {}
        for r in orders:
            if r[6] == "paid":
                exp6[cc[r[1]]] = exp6.get(cc[r[1]], Decimal(0)) + Decimal(r[5])
        claim({r[0]: Decimal(r[2]) for r in rows6} == exp6, "story 6: the join gives the totals of a recount by customer country")
        S.append(story(6, "Two files", "a join", ["--join", "--on"],
            "&#8220;The orders know the customer by a number. The country is in another file. Which country buys the most?&#8221;",
            ["<p>One hash join: the <code>--join</code> file is read whole into a bounded table, the orders stream past it. The output row is the order&#8217;s cells followed by the customer&#8217;s; a column name in both files is refused, never silently renamed. The same plan then groups by a column that came from the other file.</p>",
             term(shown(a6 + ["--format", "csv"], "orders.csv"), o6.rstrip("\n")),
             fig_hbars([(r[0], float(r[2])) for r in rows6], " &#8364;", label_w=60),
             '<div class="read"><b>Reading it.</b> %s leads with %s euros, then %s. The totals are those of a recount of the two files in Python by customer country, checked when the page is generated. <code>--join-type left</code>, <code>semi</code> and <code>anti</code> answer &#8220;who never ordered&#8221; in the same way.</div>' % (rows6[0][0], rows6[0][2], rows6[1][0])]))
        # 7 ------------------------------------------------------------------------------------------------
        big = folder / "big.csv"
        r = random.Random(5)
        with open(big, "w") as fh:
            fh.write("id,group,value\n")
            for i in range(120000):
                fh.write("%d,g%d,%d.%02d\n" % (i, r.randint(0, 99), r.randint(0, 999), r.randint(0, 99)))
        size = big.stat().st_size
        a7 = ["--group", "group", "--agg", "count,sum:value:dec(2),mean:value:dec(2)@4", "--sort", "group", "--format", "csv"]
        digs = []
        for th in (1, 2, 4):
            out = R.run(a7 + ["--threads", str(th)], "big.csv")
            digs.append(("--threads %d" % th, hashlib.sha256(out.encode()).hexdigest()[:48]))
        claim(len({h for _, h in digs}) == 1, "story 7: the same bytes at 1, 2 and 4 threads")
        S.append(story(7, "The same bytes on any number of cores", "determinism", ["--threads"],
            "&#8220;It is fast enough on four cores. But will the totals be the same as on one?&#8221;",
            ["<p>The shop has grown. A file of 120,000 rows (%.1f MB; it is generated by this page&#8217;s script, seed 5) is grouped into 100 groups with an exact sum and an exact mean, once on one thread, once on two, once on four. SHA-256 of everything the tool printed:</p>" % (size / 1e6),
             fig_hashes(digs),
             '<div class="read"><b>Reading it.</b> The three digests are identical. A group sum is exact, so the order in which threads add rows cannot change a digit; a refusal names the same row, line and column whatever the thread count. The project&#8217;s CI compares such digests across Linux x86-64, Linux arm64 and macOS arm64.</div>']))
        S.append('<section class="part" id="lab"><div class="wrap"><h2>Part II &middot; In the lab and in the field</h2><p class="lead">The same tool on four small datasets from the sciences: a growth plate, a titration, a temperature logger, a survey. The data are invented; the runs are real.</p></div></section>')
        # 8 biology ----------------------------------------------------------------------------------------
        a8 = ["--where", "compound != blank", "--group", "compound,conc_uM:dec(2)", "--agg", "count,mean:od600:dec(3)@3", "--sort", "compound"]
        o8 = R.run(a8 + ["--format", "csv"], "plate.csv")
        head8, rows8 = R.csv(a8, "plate.csv")
        concs = sorted({float(r[1]) for r in rows8})
        ser = {}
        for r_ in rows8:
            ser.setdefault(r_[0], {})[float(r_[1])] = float(r_[3])
        claim(len(rows8) == 24 and all(int(r_[2]) == 4 for r_ in rows8), "story 8: 24 groups of four wells")
        claim(ser["A"][3.0] < 0.3 < ser["B"][3.0] and ser["B"][10.0] < 0.4 and ser["C"][30.0] > 0.7 and ser["C"][3.0] > 1.0, "story 8: A has stopped the culture by 3 uM, B by 10 uM, C not by 30 uM")
        ab = ["--where", "compound = blank", "--agg", "count,mean:od600:dec(3)@4"]
        d8b = R.json(ab, "plate.csv")
        S.append(story(8, "A growth plate", "biology: groups of replicates", ["--group A,B", "mean:COL:dec(3)@3"],
            "&#8220;Three compounds, eight doses, four wells each, a blank row. Which compound stops the bacteria at the lowest dose?&#8221; &#8212; a PhD student",
            ["<p>The plate reader&#8217;s export has one row per well. Group by compound and by concentration, average the optical density of the four replicates exactly (the mean of exact decimals is rounded once, half to even, to the scale you ask for: <code>@3</code>), and leave the blank wells out.</p>",
             term(shown(a8 + ["--format", "csv"], "plate.csv"), "\n".join(o8.rstrip("\n").split("\n")[:9]) + "\n..."),
             fig_lines([(k, [ser[k][c] for c in concs], cls) for k, cls in (("A", "la"), ("B", "lb"), ("C", "lc"))], concs, "concentration, micromolar", "mean OD600, four wells"),
             '<div class="read"><b>Reading it.</b> Compound A has collapsed the culture by the 3 &micro;M wells (mean %s); B needs about three times more (%s at 10 &micro;M); C is still at %s at 30 &micro;M. Every mean is the exact mean of four decimals rounded once; the blank wells (%s of them, mean %s) are what to subtract if the lab does so. The figure is drawn from the 24 group means; the first rows of the CSV are shown above.</div>' % (num(ser["A"][3.0], 3), num(ser["B"][10.0], 3), num(ser["C"][30.0], 3), d8b["rows"][0][0], d8b["rows"][0][1])]))
        # 9 chemistry --------------------------------------------------------------------------------------
        a9 = ["--group", "analyst", "--agg", "count,mean:volume_ml:dec(2)@3,min:volume_ml:dec(2),max:volume_ml:dec(2)", "--sort", "analyst"]
        h9, rows9 = R.csv(a9, "titration.csv")
        o9 = R.run(a9 + ["--format", "csv"], "titration.csv")
        vols = {}
        for r_ in make_titration():
            vols.setdefault(r_[1], []).append(float(r_[2]))
        # a reading written with a third decimal
        (folder / "titration3.csv").write_text(files["titration.csv"].replace("24.45", "24.455", 1) if "24.45" in files["titration.csv"] else files["titration.csv"].replace(",24.", ",24.", 1))
        t3 = (folder / "titration3.csv").read_text()
        if t3 == files["titration.csv"]:
            first_row = files["titration.csv"].split("\n")[1].split(",")
            t3 = files["titration.csv"].replace(first_row[2], first_row[2] + "5", 1)
            (folder / "titration3.csv").write_text(t3)
        rc9, err9 = R.refusal(a9, "titration3.csv")
        fix9 = [a.replace("dec(2)", "dec(3)") for a in a9]
        claim(err9["rule"] == "value.decimal-scale" and err9["repair"]["kind"] == "none" and err9["detail"]["digits"] == 3 and err9["detail"]["scale"] == 2, "story 9: the third decimal is refused, with the cell named, and the tool offers no ready-made command (the plan names the column four times)")
        o9b = R.run(fix9 + ["--format", "csv"], "titration3.csv")
        exact_means = {a: sum(Decimal("%.2f" % v) for v in vs) / len(vs) for a, vs in vols.items()}
        claim(max(rows9, key=lambda r_: Decimal(r_[2]))[0] == "Marc", "story 9: Marc reads highest")
        claim(all(abs(Decimal(r_[2]) - exact_means[r_[0]]) <= Decimal("0.0005") for r_ in rows9), "story 9: each printed mean is within half a unit of the last digit of the exact mean")
        S.append(story(9, "Burette readings", "chemistry: a declared precision", ["mean:COL:dec(2)@3", "value.decimal-scale"],
            "&#8220;Three analysts, eight titrations each. Does anyone read the burette differently? And one of them wrote a third decimal.&#8221;",
            ["<p>A burette is read to a hundredth of a millilitre, so the column is declared <code>:dec(2)</code>: the mean of the readings is exact and is printed to three digits (<code>@3</code>). A reading with a third digit would have been rounded by almost any tool; here it is refused, with the line and the value.</p>",
             term(shown(a9 + ["--format", "csv"], "titration.csv"), o9.rstrip("\n")),
             fig_strip([(a, vs, sum(vs) / len(vs)) for a, vs in sorted(vols.items())], 24.0, 24.8, [24.0, 24.2, 24.4, 24.6, 24.8], "titrant volume, mL"),
             "<p>The same command on the copy of the file in which one reading has a third decimal:</p>",
             term(shown(a9 + ["--format", "csv"], "titration3.csv"), "exit %d\n%s" % (rc9, pretty(tidy(err9, ("rule", "message", "hint")) | {"detail": err9.get("detail")}))),
             term(shown(fix9 + ["--format", "csv"], "titration3.csv"), o9b.rstrip("\n")),
             '<div class="read"><b>Reading it.</b> The means are %s mL (%s), %s (%s) and %s (%s): the dots and the tick of each mean show Marc reading a little higher. The third decimal was refused instead of being rounded: the answer names the line and the value, and says what to write (<code>:dec(N)</code>, N at least 3). There is no ready-made command this time, because the plan names the column four times and the tool does not choose which to change; writing <code>:dec(3)</code> in each gives the means of all the readings at the precision they were written.</div>' % (rows9[0][2], rows9[0][0], rows9[1][2], rows9[1][0], rows9[2][2], rows9[2][0])]))
        # 10 physics ---------------------------------------------------------------------------------------
        a10 = ["--input-format", "jsonl", "--fields", "ts,temp_c", "--group", "ts:timestamp@hour", "--agg", "count,min:temp_c:float,max:temp_c:float,mean:temp_c:float"]
        h10, rows10 = R.csv(a10, "sensor.jsonl")
        o10 = R.run(a10 + ["--format", "csv"], "sensor.jsonl")
        claim(len(rows10) == 48 and all(int(r_[1]) == 12 for r_ in rows10), "story 10: 48 hours of 12 readings")
        hours = [(r_[0], float(r_[2]), float(r_[3]), float(r_[4])) for r_ in rows10]
        hi = max(hours, key=lambda h: h[2])
        lo = min(hours, key=lambda h: h[1])
        S.append(story(10, "Two days of a temperature logger", "physics: a stream of readings", ["--input-format jsonl", "--group ts:timestamp@hour"],
            "&#8220;The logger writes one JSON line every five minutes. What did the hut do over two days?&#8221;",
            ["<p>The logger&#8217;s file is JSON lines, not CSV. <code>--input-format jsonl --fields ts,temp_c</code> reads two keys of each line; <code>ts:timestamp@hour</code> groups the readings by hour. The minimum, maximum and mean are floats here (a temperature is a measurement, not an amount): the tool says so with <code>:float</code>.</p>",
             term(shown(a10 + ["--format", "csv"], "sensor.jsonl"), "\n".join(o10.rstrip("\n").split("\n")[:6]) + "\n..."),
             fig_band(hours, 8, 24),
             '<div class="read"><b>Reading it.</b> 48 hours of 12 readings each (576 lines). The warmest hour is %s, with a maximum of %.2f &#176;C; the coldest is %s, with a minimum of %.2f &#176;C. The days repeat, and the hourly mean follows the day while the band shows the spread inside the hour.</div>' % (hi[0], hi[2], lo[0], lo[1])]))
        # 11 social ----------------------------------------------------------------------------------------
        a11 = ["--where", "age:int >= 18", "--group", "district,answer", "--agg", "count,distinct:respondent", "--sort", "district"]
        h11, rows11 = R.csv(a11, "survey.csv")
        o11 = R.run(a11 + ["--format", "csv"], "survey.csv")
        a11b = ["--agg", "count,distinct:respondent"]
        d11b = R.json(a11b, "survey.csv")
        n_rows, n_resp = int(d11b["rows"][0][0]), int(d11b["rows"][0][1])
        surv = make_survey()
        exp11 = {}
        for r_ in surv:
            if r_[2] >= 18:
                exp11[(r_[1], r_[3])] = exp11.get((r_[1], r_[3]), 0) + 1
        claim({(r_[0], r_[1]): int(r_[2]) for r_ in rows11} == exp11 and n_rows > n_resp, "story 11: the counts are those of a recount, and some respondents answered twice")
        groups = []
        for d in ("Centro", "Este", "Norte", "Sur"):
            groups.append((d, {a: exp11.get((d, a), 0) for a in ("yes", "unsure", "no")}))
        S.append(story(11, "Who answered, and how many times?", "social science: counts and duplicates", ["--group A,B", "distinct:COL"],
            "&#8220;Four hundred answers about the new tram line. How do the districts split, among adults? And are there people who answered twice?&#8221;",
            ["<p>Two group keys make a crosstab; <code>count</code> counts rows and <code>distinct:respondent</code> counts people. Before any percentage, ask whether rows are people:</p>",
             term(shown(a11b, "survey.csv"), pretty(d11b)),
             term(shown(a11 + ["--format", "csv"], "survey.csv"), "\n".join(o11.rstrip("\n").split("\n")[:7]) + "\n..."),
             fig_stack(groups),
             '<div class="read"><b>Reading it.</b> The file has %d rows but only %d respondents: %d people answered more than once, and a percentage of rows would count them twice. Among adults, Sur has the largest share of yes and Norte the largest of no (the bars). The next step, a test of whether the districts differ, is a job for <a href="https://alpibrusl.github.io/cancho-stats/">cancho-stats</a>, which reads the same CSV.</div>' % (n_rows, n_resp, n_rows - n_resp)]))
        # 12 marketing: what did each channel cost and earn ----------------------------------------------------
        spend, sales = make_channels()
        files["spend.csv"] = csv_of(["week", "channel", "spend"], spend)
        files["sales.csv"] = csv_of(["order_id", "channel", "revenue"], sales)
        for nm in ("spend.csv", "sales.csv"):
            (folder / nm).write_text(files[nm])
        m1 = ["--group", "channel", "--agg", "sum:spend:dec(2)", "--sort", "channel"]
        m2 = ["--group", "channel", "--agg", "count,sum:revenue:dec(2)", "--sort", "channel"]
        h_s, r_s = R.csv(m1, "spend.csv")
        h_r, r_r = R.csv(m2, "sales.csv")
        exp_s, exp_r = {}, {}
        for _, c, v in spend:
            exp_s[c] = exp_s.get(c, Decimal(0)) + Decimal(v)
        for _, c, v in sales:
            exp_r[c] = exp_r.get(c, Decimal(0)) + Decimal(v)
        claim({r[0]: Decimal(r[1]) for r in r_s} == exp_s and {r[0]: Decimal(r[2]) for r in r_r} == exp_r, "story 12: both groupings equal a recount in decimals")
        roas = {r[0]: Decimal(r[2]) / Decimal(sp[1]) for r, sp in zip(r_r, r_s)}
        claim(max(roas, key=roas.get) == "email" and min(roas, key=roas.get) == "display" and roas["email"] > roas["search"] > roas["social"] > roas["display"], "story 12: from the most to the least per euro: email, search, social, display")
        S.append(story(12, "What did each channel cost, and earn?", "marketing: two files, exact money", ["--group", "sum:COL:dec(2)"],
            "&#8220;Finance wants spend and revenue by channel, to the cent, and wants it to add up.&#8221; &#8212; a marketing analyst, on the first of the month",
            ["<p>Two exports: the ad platforms&#8217; weekly spend (<code>spend.csv</code>) and the orders the shop credits to a channel by last click (<code>sales.csv</code>). Money is the case <code>:dec(2)</code> exists for: each sum is the exact sum of the cents, and a cell with a third decimal would be refused, never rounded.</p>",
             term(shown(m1 + ["--format", "csv"], "spend.csv"), "\n".join(",".join(r) for r in [h_s] + r_s)),
             term(shown(m2 + ["--format", "csv"], "sales.csv"), "\n".join(",".join(r) for r in [h_r] + r_r)),
             fig_hbars([(r[0], float(Decimal(r[2]) / Decimal(sp[1]))) for r, sp in zip(r_r, r_s)], " per euro", fmt="%.2f", label_w=70),
             '<div class="read"><b>Reading it.</b> Dividing the second table by the first (the page does that arithmetic, on the printed numbers) gives the revenue credited per euro of spend: %s euros for email, %s for search, %s for display. Both files were recounted in Python&#8217;s <code>decimal</code> when this page was generated. One warning belongs next to the table: <em>credited</em> is not <em>caused</em>. Last click credits whoever the customer touched last, and a search ad is often the last thing touched by someone who had already decided; whether an ad earns money that would not have come anyway is the question of stories 12 to 14 of cancho-stats.</div>' % ("%.2f" % roas["email"], "%.2f" % roas["search"], "%.2f" % roas["display"])]))
        # 13 marketing: the holdout table ---------------------------------------------------------------------
        users, purchases = make_campaign()
        files["campaign_users.csv"] = csv_of(["user_id", "arm"], users)
        files["campaign_purchases.csv"] = csv_of(["purchase_id", "user_id", "date", "amount"], purchases)
        for nm in ("campaign_users.csv", "campaign_purchases.csv"):
            (folder / nm).write_text(files[nm])
        k1 = ["--group", "arm", "--agg", "count", "--sort", "arm"]
        k2 = ["--join", "campaign_users.csv", "--on", "user_id", "--where", "date:date >= 2026-09-01", "--group", "arm", "--agg", "count,distinct:user_id,sum:amount:dec(2)", "--sort", "arm"]
        h1_, r1_ = R.csv(k1, "campaign_users.csv")
        h2_, r2_ = R.csv(k2, "campaign_purchases.csv")
        arm_of = dict(users)
        exp_buyers = {}
        exp_rev = {}
        for _, u, d, v in purchases:
            if d >= "2026-09-01":
                exp_buyers.setdefault(arm_of[u], set()).add(u)
                exp_rev[arm_of[u]] = exp_rev.get(arm_of[u], Decimal(0)) + Decimal(v)
        claim({r[0]: int(r[2]) for r in r2_} == {a: len(b) for a, b in exp_buyers.items()} and {r[0]: Decimal(r[3]) for r in r2_} == exp_rev, "story 13: buyers and revenue per arm equal a recount that drops purchases before September")
        pre = sum(1 for p in purchases if p[2] < "2026-09-01")
        claim(pre > 100 and [r[1] for r in r1_] == ["4000", "4000"] and int(r2_[0][2]) > int(r2_[1][2]), "story 13: some purchases predate the window, the arms are equal in size and the shown arm has more buyers")
        S.append(story(13, "From the raw tables to the holdout analysis", "marketing: join, window, per-arm totals", ["--join", "--where date:date", "distinct:COL"],
            "&#8220;The experiment tool gave me who was shown the ad and who was held out. The shop gave me purchases. I need, per arm: users, buyers, revenue.&#8221;",
            ["<p>Two tables, and the counting rules matter more than the tool: purchases made <em>before</em> the campaign started do not belong to it, and a user who bought twice is one buyer. First the denominators, then one join of the purchases to the arm of each user, restricted to the campaign window, counting purchases, <em>distinct</em> buyers and the exact revenue:</p>",
             term(shown(k1 + ["--format", "csv"], "campaign_users.csv"), "\n".join(",".join(r) for r in [h1_] + r1_)),
             term(shown(k2 + ["--format", "csv"], "campaign_purchases.csv"), "\n".join(",".join(r) for r in [h2_] + r2_)),
             fig_hbars([(r[0], float(r[2])) for r in r2_], " buyers", fmt="%.0f", label_w=70),
             '<div class="read"><b>Reading it.</b> %s buyers among 4000 users shown the ad, %s among 4000 held out; revenue %s and %s euros. %d purchases in the file were made in August, before the start, and the <code>--where</code> left them out; counting them would have made both arms look better and the campaign look no different. These four numbers per arm are what the significance question needs: <code>stats ci prop</code> on the buyer counts, and <code>stats ci diff</code> or <code>resample perm</code> on the per-user revenue (see cancho-stats, story 12, for exactly this analysis). Producing a per-user file for it is one more <code>--group user_id</code>.</div>' % (r2_[0][2], r2_[1][2], r2_[0][3], r2_[1][3], pre)]))
    return S, files


def page(sections):
    css = base_css("#0b7a75", "#4fd1c5", "#ffffff", "#06201e") + STORY_CSS + EXTRA_CSS
    nav = " ".join('<a class="opt" href="#s%d">%d</a>' % (i, i) for i in range(1, 14))
    names = ("First look", "A question", "Decimals", "Refusals", "By month", "A join", "Cores", "Plate", "Titration", "Logger", "Survey", "Channels", "Holdout")
    toc = "".join('<a href="#s%d">%d &middot; %s</a>' % (i, i, esc(nm)) for i, nm in enumerate(names, 1))
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>table: thirteen stories, from an order export to the lab</title>
<meta name="description" content="Thirteen end-to-end examples of the table command, in increasing complexity: an online shop, then biology, chemistry, physics and a social survey. Every number and figure is drawn from a real run.">
<meta name="color-scheme" content="light dark">
<style>%s</style>
</head>
<body>
<header class="top"><div class="wrap">
  <a class="brand" href=".">table</a>
  <nav>%s<a href=".">Overview</a><a href="https://github.com/alpibrusl/cancho-table">GitHub</a></nav>
</div></header>
<div class="wrap hero small">
  <h1>Thirteen stories, from an order export to the lab.</h1>
  <p class="sub">Luc&#237;a sells ceramics online and asks her order file one question after another; each asks a little more of <code>table</code> than the last. Then four short ones from the sciences and two from marketing. Every number and every figure is drawn from a real run of <code>table</code> on the data you can download. The data are invented; the runs are real, and the narration is checked against the numbers whenever the page is generated.</p>
  <div class="toc">%s</div>
  <p class="data">Data: <a href="stories/orders.csv">orders.csv</a>, <a href="stories/customers.csv">customers.csv</a>, <a href="stories/plate.csv">plate.csv</a>, <a href="stories/titration.csv">titration.csv</a>, <a href="stories/sensor.jsonl">sensor.jsonl</a>, <a href="stories/spend.csv">spend.csv</a>, <a href="stories/sales.csv">sales.csv</a>, <a href="stories/campaign_users.csv">campaign_users.csv</a>, <a href="stories/campaign_purchases.csv">campaign_purchases.csv</a>, <a href="stories/survey.csv">survey.csv</a>. Rebuild this page: <code>python3 scripts/gen_stories.py</code>.</p>
</div>
%s
<footer><div class="wrap">table &middot; EUPL-1.2 &middot; the data are invented; the runs, the digits and the figures are real &middot; <a href=".">overview</a></div></footer>
</body>
</html>
""" % (css, nav, toc, "\n".join(sections))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    sections, files = build(a.bin)
    out = {DOCS / "stories.html": page(sections)}
    for name, text in files.items():
        out[DOCS / "stories" / name] = text
    if a.check:
        stale = [str(p.relative_to(ROOT)) for p, text in out.items() if not p.exists() or p.read_text() != text]
        if stale:
            print("gen_stories: stale: " + ", ".join(stale))
            return 1
        print("gen_stories: up to date (%d files)" % len(out))
        return 0
    for p, text in out.items():
        p.parent.mkdir(exist_ok=True)
        p.write_text(text)
    print("gen_stories: wrote %d files" % len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
