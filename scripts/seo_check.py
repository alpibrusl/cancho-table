#!/usr/bin/env python3
"""A small check of the SEO basics of the site pages (standard library only):

    python3 scripts/seo_check.py

For docs/index.html and docs/benchmarks.html: exactly one <title> of 50 to 60
characters, one meta description of at most 160, one canonical link on the site's
base address, a lang attribute, a viewport, Open Graph and Twitter card tags, one
<h1>, headings that never skip a level, every <img> with an alt attribute, JSON-LD
that parses, no <script src> at all. And: every URL in docs/sitemap.xml is a file
of docs/, and docs/robots.txt names the sitemap. Exit 1 on the first failure list.
"""
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = re.search(r'SITE_BASE = "([^"]+)"', (ROOT / "scripts" / "site.py").read_text()).group(1)
PAGES = {"index.html": BASE, "benchmarks.html": BASE + "benchmarks.html"}


class P(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.metas, self.links, self.imgs, self.heads, self.ld = [], [], [], [], [], []
        self.title = ""
        self._in = None
        self.lang = None
        self.scripts_src = 0
        self.svg = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "svg":
            self.svg += 1
        if tag == "title" and self.svg:
            return  # the <title> of an inline SVG is its accessible name
        self.tags.append(tag)
        if tag == "html":
            self.lang = a.get("lang")
        if tag == "meta":
            self.metas.append(a)
        if tag == "link":
            self.links.append(a)
        if tag == "img":
            self.imgs.append(a)
        if tag == "script":
            if a.get("src"):
                self.scripts_src += 1
            if a.get("type") == "application/ld+json":
                self._in = "ld"
                self.ld.append("")
        if tag == "title" and not self.svg:
            self._in = "title"
        if re.fullmatch(r"h[1-6]", tag):
            self.heads.append(int(tag[1]))

    def handle_endtag(self, tag):
        if tag == "svg":
            self.svg -= 1
        if tag in ("script", "title"):
            self._in = None

    def handle_data(self, d):
        if self._in == "title":
            self.title += d
        elif self._in == "ld":
            self.ld[-1] += d


def check(name, url, errs):
    p = P()
    p.feed((ROOT / "docs" / name).read_text())
    def meta(key, val):
        return [m.get("content", "") for m in p.metas if m.get(key) == val]
    def need(ok, msg):
        if not ok:
            errs.append("%s: %s" % (name, msg))
    need(p.tags.count("title") == 1, "exactly one <title>")
    need(50 <= len(p.title) <= 60, "title is %d characters, want 50 to 60" % len(p.title))
    d = meta("name", "description")
    need(len(d) == 1 and 0 < len(d[0]) <= 160, "exactly one description of at most 160 characters")
    c = [l.get("href") for l in p.links if l.get("rel") == "canonical"]
    need(c == [url], "canonical is %r, want %r" % (c, url))
    need(bool(p.lang), "html lang")
    need(len(meta("name", "viewport")) == 1, "viewport")
    for k in ("og:title", "og:description", "og:url", "og:image", "og:type"):
        need(len(meta("property", k)) == 1, k)
    for k in ("twitter:card", "twitter:title", "twitter:description", "twitter:image"):
        need(len(meta("name", k)) == 1, k)
    need(meta("property", "og:url") == [url], "og:url equals the canonical")
    need(p.heads.count(1) == 1, "exactly one <h1>")
    need(all(b - a <= 1 for a, b in zip(p.heads, p.heads[1:])), "headings skip a level")
    need(all("alt" in i for i in p.imgs), "an <img> without alt")
    need(p.scripts_src == 0, "an external <script src>")
    need(len(p.ld) >= 1, "JSON-LD")
    for blob in p.ld:
        try:
            doc = json.loads(blob)
        except ValueError as exc:
            errs.append("%s: JSON-LD does not parse: %s" % (name, exc))
            continue
        need(doc.get("url") == url, "JSON-LD url is the canonical")
    return p


def main():
    errs = []
    titles = set()
    for name, url in PAGES.items():
        p = check(name, url, errs)
        titles.add(p.title)
    if len(titles) != len(PAGES):
        errs.append("titles are not unique")
    sm = (ROOT / "docs" / "sitemap.xml").read_text()
    for loc in re.findall(r"<loc>([^<]+)</loc>", sm):
        if not loc.startswith(BASE):
            errs.append("sitemap: %s is not on the base address" % loc)
            continue
        rel = loc[len(BASE):] or "index.html"
        if not (ROOT / "docs" / rel).is_file():
            errs.append("sitemap: %s has no file docs/%s" % (loc, rel))
    if "Sitemap: " + BASE + "sitemap.xml" not in (ROOT / "docs" / "robots.txt").read_text():
        errs.append("robots.txt does not name the sitemap")
    for e in errs:
        print("seo_check:", e)
    if not errs:
        print("seo_check: %d pages, sitemap and robots.txt are consistent" % len(PAGES))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
