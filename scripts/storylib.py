#!/usr/bin/env python3
"""The parts of the stories pages that do not depend on the tool: the terminal block, the figure frame and axes, the narration check, the page frame. cancho-stats, cancho-table and cancho-ml each carry the same copy of this
file (scripts/storylib.py) so that each repository builds its own page alone; change the three together."""
import html
import json
import re

W = 640


def claim(cond, what):
    """the narration says something about the numbers: if the numbers stop saying it, the page is not generated"""
    if not cond:
        raise SystemExit("gen_stories: the narration no longer matches the runs: " + what)


def pretty(obj, width=78, indent=0):
    """indented JSON in which every object or list that fits on one line is kept on one line"""
    flat = json.dumps(obj, ensure_ascii=False)
    pad = "  " * indent
    if len(flat) + len(pad) <= width or not isinstance(obj, (dict, list)) or not obj:
        return flat
    if isinstance(obj, dict):
        items = ["%s  %s: %s" % (pad, json.dumps(k, ensure_ascii=False), pretty(v, width, indent + 1)) for k, v in obj.items()]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    items = ["%s  %s" % (pad, pretty(v, width, indent + 1)) for v in obj]
    return "[\n" + ",\n".join(items) + "\n" + pad + "]"


def num(x, k=4):
    s = ("%." + str(k) + "g") % x
    return s


# ------------------------------------------------------------------------------------------------ figures (inline SVG, colours from the page's variables)


def esc(s):
    return html.escape(str(s), quote=True)


def axis(x0, x1, y, lo, hi, ticks, label, fmt=lambda v: "%g" % v):
    sx = lambda v: x0 + (v - lo) / (hi - lo) * (x1 - x0)
    out = ['<line class="ax" x1="%g" y1="%g" x2="%g" y2="%g"/>' % (x0, y, x1, y)]
    for t in ticks:
        out.append('<line class="ax" x1="%g" y1="%g" x2="%g" y2="%g"/><text class="tk" x="%g" y="%g" text-anchor="middle">%s</text>' % (sx(t), y, sx(t), y + 5, sx(t), y + 19, esc(fmt(t))))
    out.append('<text class="lb" x="%g" y="%g" text-anchor="middle">%s</text>' % ((x0 + x1) / 2, y + 38, esc(label)))
    return "\n".join(out), sx


def svg(h, body, label):
    return '<figure class="fig"><svg viewBox="0 0 %d %d" role="img" aria-label="%s">%s</svg></figure>' % (W, h, esc(label), body)


def wrap_cmd(command, width=84):
    out, line = [], ""
    for word in command.split(" "):
        if line and len(line) + 1 + len(word) > width:
            out.append(line + " \\")
            line = "    " + word
        else:
            line = (line + " " + word) if line else word
    out.append(line)
    return "\n".join(out)


def term(command, output):
    command = wrap_cmd(command)
    return '<div class="term"><div class="bar"><i></i><i></i><i></i> a real run</div><pre><span class="cm">$ %s</span>\n%s</pre></div>' % (esc(command), esc(output))


def story(n, title, level, verbs, said, parts):
    body = "".join(parts)
    badges = '<span class="lvl">%s</span>' % esc(level) + "".join("<span>%s</span>" % esc(v) for v in verbs)
    return '<section class="%s story" id="s%d"><div class="wrap"><h2>%d &middot; %s</h2><div class="badge">%s</div><p class="said">%s</p>%s</div></section>' % ("alt" if n % 2 == 0 else "", n, n, esc(title), badges, said, body)


def fig_hashes(rows):
    body = []
    for k, (label, h) in enumerate(rows):
        y = 28 + k * 34
        body.append('<text class="nm" x="205" y="%g" text-anchor="end">%s</text><rect class="hash" x="215" y="%g" width="395" height="22" rx="4"/><text class="hx" x="225" y="%g">%s</text>' % (y + 5, esc(label), y - 10, y + 5, esc(h)))
    return svg(28 + 34 * len(rows) + 10, "\n".join(body), "The digests of the answers of the same question asked with the rows in different orders")


# ------------------------------------------------------------------------------------------------ the page


STORY_CSS = """
.hero.small { padding: 3rem 0 1rem; }
.story { padding: 3rem 0; }
.story .wrap { max-width: 50rem; }
.story h2 { text-align: left; margin-bottom: .3rem; }
.badge { display: inline-flex; gap: .4rem; flex-wrap: wrap; margin: 0 0 1rem; }
.badge span { font: .78rem var(--mono); color: var(--muted); border: 1px solid var(--line); border-radius: 99px; padding: .1rem .6rem; background: var(--surface); }
.badge .lvl { color: var(--accent); border-color: var(--accent); }
.said { border-left: 3px solid var(--accent); padding: .2rem 0 .2rem 1rem; margin: 1rem 0; color: var(--muted); font-style: italic; }
.story .term { max-width: none; margin: 1rem 0; box-shadow: none; }
.story .term pre { min-height: 0; white-space: pre; word-break: normal; font-size: .8rem; }
.story pre .cm { color: var(--term-acc); }
.read { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 1rem 1.2rem; margin: 1rem 0; }
.read b { color: var(--accent); }
figure.fig { margin: 1rem 0; }
figure.fig svg { width: 100%; height: auto; display: block; background: var(--surface); border: 1px solid var(--line); border-radius: 12px; }
.fig .ax { stroke: var(--muted); stroke-width: 1; }
.fig .tk { fill: var(--muted); font: 12px var(--font); }
.fig .lb { fill: var(--muted); font: 13px var(--font); }
.fig .nm { fill: var(--fg); font: 600 13px var(--font); }
.fig .wh { stroke-width: 2; } .fig .wh.pa, .fig .bx.pa { stroke: var(--accent); } .fig .wh.pb, .fig .bx.pb { stroke: var(--good); }
.fig .bx { fill: var(--soft); stroke-width: 2; }
.fig .md { stroke: var(--fg); stroke-width: 3; }
.fig .mn { fill: var(--warn); }
.fig .zero { stroke: var(--bad); stroke-width: 1.5; stroke-dasharray: 5 4; }
.fig .ciline { stroke: var(--accent); stroke-width: 3; }
.fig .pt { fill: var(--accent); }
.fig .band { stroke: var(--line); stroke-width: 1; }
.fig .dot.pa { fill: var(--accent); opacity: .8; } .fig .dot.pb { fill: var(--good); opacity: .8; }
.fig .ring { fill: none; stroke: var(--bad); stroke-width: 2; }
.fig .pbar.pa { fill: var(--accent); } .fig .pbar.pb { fill: var(--good); } .fig .pbar.warn { fill: var(--warn); }
.fig .link { stroke: var(--muted); stroke-width: 1.5; }
.fig .raw { fill: var(--surface); stroke: var(--accent); stroke-width: 2.5; } .fig .adj { fill: var(--accent); }
.fig .cell { fill: var(--soft); stroke: var(--line); } .fig .cell.hit { fill: var(--bad); stroke: var(--bad); }
.fig .seg { fill: var(--good); } .fig .seg.bad { fill: var(--bad); }
.fig .hash { fill: var(--soft); stroke: var(--good); stroke-width: 1.5; }
.fig .hx { fill: var(--fg); font: 12px var(--mono); }
.fig .bandci { fill: var(--accent); opacity: .18; }
.fig .ecdfa { fill: none; stroke: var(--accent); stroke-width: 2.5; } .fig .ecdfb { fill: none; stroke: var(--good); stroke-width: 2.5; }
.fig .gap { stroke: var(--bad); stroke-width: 3; }
.fig .lk1 { fill: #b42d27; } .fig .lk2 { fill: #d97a52; } .fig .lk3 { fill: #c9c9c9; } .fig .lk4 { fill: #5a9bd4; } .fig .lk5 { fill: #1f5fa6; }
.fig .lk { fill: #fff; font: 600 12px var(--font); }
.part { padding: 2.5rem 0 .5rem; text-align: center; border-top: 1px solid var(--line); }
.part h2 { text-align: center; }
.toc { display: flex; flex-wrap: wrap; gap: .6rem; justify-content: center; margin-top: 1.2rem; }
.toc a { text-decoration: none; border: 1px solid var(--line); border-radius: 99px; padding: .25rem .9rem; background: var(--surface); font-size: .9rem; }
.data { color: var(--muted); font-size: .9rem; text-align: center; margin-top: 1rem; }
"""


BASE_CSS = r"""
:root {
  --bg: #fbfcfd; --fg: #14181a; --muted: #55606a; --line: #dfe5e7; --surface: #ffffff; --soft: #eff4f4;
  --accent: @ACCENT@; --accent-fg: @ACCENT_FG@; --good: #0f7b5f; --bad: #b42d27; --warn: #92400e;
  --term-bg: #0f1519; --term-fg: #d6e2e6; --term-dim: #7d8f96; --term-good: #5fd3a6; --term-bad: #ff8a80; --term-acc: #8ec0ff;
  --font: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #0b0f14; --fg: #e7ebec; --muted: #9aa8ae; --line: #212a2d; --surface: #131a1c; --soft: #172022;
    --accent: @ACCENT_DARK@; --accent-fg: @ACCENT_FG_DARK@; --good: #4fd1a5; --bad: #ff7b73; --warn: #f3c77a; --term-bg: #0a1013; }
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; -webkit-text-size-adjust: 100%; scroll-padding-top: 4rem; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 1rem/1.6 var(--font); }
a { color: var(--accent); text-underline-offset: .15em; }
.wrap { width: 100%; max-width: 64rem; margin: 0 auto; padding: 0 16px; }
header.top { position: sticky; top: 0; z-index: 5; background: color-mix(in srgb, var(--bg) 88%, transparent); backdrop-filter: blur(8px); border-bottom: 1px solid var(--line); }
header.top .wrap { display: flex; align-items: center; gap: 1.5rem; height: 3.4rem; }
.brand { font: 700 1rem var(--mono); color: var(--fg); text-decoration: none; }
nav { display: flex; gap: 1.2rem; margin-left: auto; }
nav a { color: var(--muted); text-decoration: none; font-size: .93rem; }
nav a:hover { color: var(--fg); }
@media (max-width: 40rem) { nav a.opt { display: none; } }
.hero { padding: 3.5rem 0 2rem; text-align: center; }
.hero h1 { font-size: clamp(2rem, 6vw, 3.4rem); line-height: 1.1; margin: 0 auto 1rem; letter-spacing: -.03em; max-width: 18ch; }
.hero .sub { font-size: 1.2rem; color: var(--muted); max-width: 36rem; margin: 0 auto 1.6rem; }
.cta { display: flex; gap: .8rem; justify-content: center; flex-wrap: wrap; }
.btn { display: inline-block; padding: .65rem 1.2rem; border-radius: 8px; font-weight: 600; text-decoration: none; border: 1px solid var(--line); color: var(--fg); background: var(--surface); }
.btn.primary { background: var(--accent); color: var(--accent-fg); border-color: var(--accent); }
section { margin: 0; padding: 3rem 0; }
section.alt { background: var(--soft); }
h2 { font-size: clamp(1.4rem, 3vw, 1.9rem); margin: 0 0 .6rem; letter-spacing: -.02em; text-align: center; }
.lead { text-align: center; color: var(--muted); max-width: 38rem; margin: 0 auto 2rem; }
.term { background: var(--term-bg); color: var(--term-fg); border-radius: 12px; border: 1px solid var(--line); max-width: 46rem; margin: 0 auto; box-shadow: 0 10px 30px rgba(0,0,0,.18); overflow: hidden; }
.term .bar { display: flex; align-items: center; gap: .4rem; padding: .55rem .8rem; border-bottom: 1px solid #1f2a30; font: .78rem var(--mono); color: var(--term-dim); }
.term .bar i { width: .7rem; height: .7rem; border-radius: 50%; background: #2c3a41; display: inline-block; }
.term .bar button { margin-left: auto; font: .78rem var(--mono); color: var(--term-fg); background: #1b262c; border: 1px solid #2c3a41; border-radius: 6px; padding: .2rem .6rem; cursor: pointer; }
.term pre { margin: 0; padding: 1rem 1.1rem; min-height: 17.5rem; overflow-x: auto; font: .85rem/1.55 var(--mono); white-space: pre-wrap; word-break: break-all; }
.term .g { color: var(--term-good); } .term .b { color: var(--term-bad); } .term .d { color: var(--term-dim); } .term .a { color: var(--term-acc); }
code { font: .9em var(--mono); }
footer { border-top: 1px solid var(--line); padding: 1.5rem 0 2rem; color: var(--muted); font-size: .9rem; text-align: center; }
"""


def base_css(accent, accent_dark, accent_fg="#ffffff", accent_fg_dark="#07162c"):
    """the page's own base styles, with the project's accent colour (cancho-table and cancho-ml use it; cancho-stats takes the styles of its index page)"""
    return BASE_CSS.replace("@ACCENT@", accent).replace("@ACCENT_DARK@", accent_dark).replace("@ACCENT_FG@", accent_fg).replace("@ACCENT_FG_DARK@", accent_fg_dark)
