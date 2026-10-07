#!/usr/bin/env python3
"""Rename lex-sys / lexsys to cancho in this repository: paths first (git mv), then text, then the pins.

    python3 scripts/migrate_to_cancho.py --dry-run        # list what would change, touch nothing
    python3 scripts/migrate_to_cancho.py                  # apply, on a clean checkout of main
    python3 scripts/migrate_to_cancho.py --regenerate     # apply, then rebuild the generated files (needs `cancho`)

It is mechanical on purpose: run it on the tip of main and it never conflicts, whatever the open branches added
since it was written. It is also idempotent: on a migrated tree it changes nothing and says so.

What it does, as lexsys-tools' own migration (cancho-tools #30) and the compiler's (cancho #347,
scripts/rename_to_cancho.py) did:

  1. paths: every tracked path segment lex-sys / lex_sys / lexsys becomes cancho (lex-sys.toml -> cancho.toml),
     every `*.ls` becomes `*.cho`; `git mv`, so history follows.
  2. text: lexsys-table -> cancho-table, lexsys-tools -> cancho-tools, lex-sys / lex_sys / lexsys -> cancho,
     LEX_SYS -> CANCHO (so LEX_SYS_ARGS -> CANCHO_ARGS), .lex-sys-vcs -> .cancho-vcs, `.ls` -> `.cho`.
     Not touched: LICENSE, binary files, this script and its test, lines carrying the history note's marker.
  3. pins, in cancho.toml: `cancho = "<rev>"` under [package] is the compiler these sources were written for, and
     every `rev = ` of a [dependencies.*] on cancho-tools is the revision of its migrated contract package
     (the stores are `.cancho-vcs/toolbox.NAME`). The defaults are the ones cancho-tools itself pins.
  4. docs/history.md gets one line saying the language was renamed and old names appear in the record.
  5. --regenerate: `cancho install`, scripts/schemas.py, scripts/manifest.py, `cancho build`, scripts/site.py
     (the generated files, the manifests, the schemas' $id and the generated regions of the pages are written by
     those scripts, not edited by hand; the compiler pin they embed changes).

The GitHub repository itself is renamed by hand, after the merge (see the runbook in the pull request).
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# cancho-tools at the merge of its rename (#30): the contract package's stores are .cancho-vcs/toolbox.NAME.
CONTRACT_REV = "8dab9176e521660333914dc768a80a29330a2a8e"
# cancho at the commit cancho-tools pins in its cancho.toml (the rename #347, source-compatible with the
# f8ebe98 these sources were written for; verified by the full gate run in the pull request).
COMPILER_REV = "a4572ea3b506e4bcec1c4be43deda1043c11ba9f"

SELF = {"scripts/migrate_to_cancho.py", "tests/test_migrate_to_cancho.py"}
NEVER = {"LICENSE"}
MARKER = "<!-- cancho-rename-note -->"
NOTE = (
    "> The language was renamed from lex-sys to cancho (source extension `.ls` to `.cho`), and the repositories "
    "lexsys-table and lexsys-tools to cancho-table and cancho-tools. Names are current throughout this file; the "
    "record below was written under the old ones, and a pull request or commit it cites is the same one under "
    "the new repository name (GitHub redirects the old URLs). " + MARKER
)

# Order matters: the longer, more specific names first.
SUBS = [
    (re.compile(r"lexsys-table"), "cancho-table"),
    (re.compile(r"lexsys-tools"), "cancho-tools"),
    (re.compile(r"lex-sys"), "cancho"),
    (re.compile(r"lex_sys"), "cancho"),
    (re.compile(r"lexsys"), "cancho"),
    (re.compile(r"LEX_SYS"), "CANCHO"),
    (re.compile(r"LEXSYS"), "CANCHO"),
    (re.compile(r"\.ls\b"), ".cho"),
]


def git(root, *args, check=True):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=check).stdout


def tracked(root):
    return [p for p in git(root, "ls-files", "-z").split("\0") if p]


def new_path(p):
    out = []
    for seg in p.split("/"):
        for old in ("lex-sys", "lex_sys", "lexsys"):
            seg = seg.replace(old, "cancho")
        out.append(seg)
    q = "/".join(out)
    return q[:-3] + ".cho" if q.endswith(".ls") else q


def rewrite(text):
    """The new text, line by line, so that the history note (which names the old names) is left alone."""
    out = []
    for line in text.split("\n"):
        if MARKER not in line:
            for rx, rep in SUBS:
                line = rx.sub(rep, line)
        out.append(line)
    return "\n".join(out)


def read_text(path):
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def pin_project(text, compiler, contract):
    """cancho.toml: the compiler pin under [package], the contract revision under every cancho-tools dependency."""
    out, section, git_is_tools = [], "", False
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("["):
            section, git_is_tools = s, False
        elif section == "[package]" and re.match(r'^cancho\s*=\s*"[0-9a-f]+"', s):
            line = re.sub(r'"[0-9a-f]+"', '"%s"' % compiler, line, count=1)
        elif section.startswith("[dependencies.") and re.match(r'^git\s*=\s*".*cancho-tools"', s):
            git_is_tools = True
        elif section.startswith("[dependencies.") and git_is_tools and re.match(r'^rev\s*=\s*"[0-9a-f]+"', s):
            line = re.sub(r'"[0-9a-f]+"', '"%s"' % contract, line, count=1)
        out.append(line)
    return "\n".join(out)


def add_history_note(text):
    if MARKER in text:
        return text
    lines = text.split("\n")
    at = next((i for i, l in enumerate(lines) if l.startswith("# ")), -1) + 1
    return "\n".join(lines[:at] + ["", NOTE] + lines[at:])


def plan(root, compiler, contract):
    """(moves, edits): the git mv's, and {new path: new text} for every file whose text changes."""
    files = tracked(root)
    moves = [(p, new_path(p)) for p in files if new_path(p) != p]
    moved = dict(moves)
    edits = {}
    for p in files:
        q = moved.get(p, p)
        if p in SELF or q in SELF or q in NEVER or p in NEVER:
            continue
        text = read_text(root / p)
        if text is None:
            continue
        new = rewrite(text)
        if q == "cancho.toml":
            new = pin_project(new, compiler, contract)
        if q == "docs/history.md":
            new = add_history_note(new)
        if new != text:
            edits[q] = new
    return moves, edits


def run(cmd, root, env=None):
    print("+", " ".join(cmd))
    r = subprocess.run(cmd, cwd=root, env=env)
    if r.returncode != 0:
        sys.exit("migrate: %s failed (%d)" % (" ".join(cmd), r.returncode))


def regenerate(root):
    compiler = os.environ.get("CANCHO", "cancho")
    py = sys.executable
    run([compiler, "install"], root)
    run([py, "scripts/schemas.py"], root)
    run([py, "scripts/manifest.py"], root)
    run([compiler, "build"], root)
    run([py, "scripts/manifest.py"], root)
    run([py, "scripts/site.py"], root)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help="the checkout (default: this one)")
    ap.add_argument("--dry-run", action="store_true", help="list what would change; change nothing")
    ap.add_argument("--regenerate", action="store_true", help="afterwards: install, schemas, manifest, build, site (needs cancho)")
    ap.add_argument("--compiler-rev", default=COMPILER_REV, help="the cancho commit the sources are pinned to")
    ap.add_argument("--contract-rev", default=CONTRACT_REV, help="the cancho-tools commit of the contract package")
    ap.add_argument("--allow-dirty", action="store_true", help="apply on a tree with uncommitted changes")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    if git(root, "rev-parse", "--is-inside-work-tree", check=False).strip() != "true":
        sys.exit("migrate: %s is not a git checkout" % root)

    moves, edits = plan(root, args.compiler_rev, args.contract_rev)
    for old, new in moves:
        print("%s %s -> %s" % ("would move" if args.dry_run else "move", old, new))
    for path in sorted(edits):
        print("%s %s" % ("would edit" if args.dry_run else "edit", path))
    if not moves and not edits:
        print("already migrated: nothing to change")
    elif args.dry_run:
        print("%d path(s) to move, %d file(s) to edit (dry run: nothing changed)" % (len(moves), len(edits)))
    else:
        if not args.allow_dirty and git(root, "status", "--porcelain").strip():
            sys.exit("migrate: the working tree is not clean (commit or stash, or --allow-dirty)")
        for old, new in moves:
            (root / new).parent.mkdir(parents=True, exist_ok=True)
            git(root, "mv", old, new)
        for path, text in edits.items():
            with open(root / path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        print("%d path(s) moved, %d file(s) edited" % (len(moves), len(edits)))
    if args.regenerate and not args.dry_run:
        regenerate(root)


if __name__ == "__main__":
    main()
