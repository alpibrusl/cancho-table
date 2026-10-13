#!/usr/bin/env python3
"""Publish the numeric modules of tools/table as `.cancho-vcs` stores, one store per module, each requiring the stores of the modules
it imports (cancho docs/package-system.md 7.4). docs/numeric-package.md says what is checked and why.

    python3 scripts/publish_numeric.py            # rewrite .cancho-vcs/<module> at the repository root
    python3 scripts/publish_numeric.py --check    # change nothing; exit 1 if the committed stores are not what a fresh publish gives

--src DIR and --out DIR (both default to the repository root) read the sources from, and write the stores to, other places.
The compiler is $CANCHO, or `cancho` on PATH; it must be the commit cancho.toml names, because the bytes of a store depend on it.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the modules another repository reads cells with: the cell parsers (pow5, dec, flt, query), the exact float sum (facc) and RFC 4180 records (reader)
MODULES = ["pow5", "dec", "flt", "query", "facc", "reader"]
STORE = ".cancho-vcs"
# The rule of what may be published (docs/numeric-package.md): a published module is an interface another repository pins, so it imports nothing but
# `std.*` and other published modules, and it is not the table's own plan. `Query` (names, conditions, aggregates, meta) is what ten internal modules
# share; a module that declares it is internal whatever else it holds (#44 published `query` for the three cell parsers it then held; they are in `cells`).
PLAN_STRUCT = re.compile(r"^\s*(?:pub\s+)?(?:res\s+)?struct\s+Query\b", re.M)


def compiler(src):
    exe = os.environ.get("CANCHO") or shutil.which("cancho")
    if not exe:
        raise SystemExit("no compiler: set CANCHO, or put the cancho named in cancho.toml on PATH")
    pin = re.search(r'^cancho\s*=\s*"([0-9a-f]+)"', open(os.path.join(src, "cancho.toml")).read(), re.M)
    got = subprocess.run([exe, "--version"], stdout=subprocess.PIPE, text=True, check=True).stdout
    rev = re.search(r"rev ([0-9a-f]+)", got)
    if not pin or not rev or rev.group(1) != pin.group(1):
        raise SystemExit(f"{exe} is {rev.group(1) if rev else got.strip()}, cancho.toml pins {pin.group(1) if pin else '?'}")
    return exe


def interface_problems(text, module, imports):
    """why `module` (its source `text`, its non-std `imports`) may not be published, as a list of sentences"""
    bad = []
    if PLAN_STRUCT.search(text):
        bad.append(f"{module} declares the table's plan struct `Query`: it is an internal module, not a stable interface")
    return bad + [f"{module} imports {i}, which is not published: add it to MODULES or cut the import" for i in imports if i not in MODULES]


def sources(src):
    """{module: (file, [imports that are not std])}; refuses a module that is not a stable interface (interface_problems)"""
    found = {}
    problems = []
    for m in MODULES:
        path = os.path.join(src, "tools", "table", m + ".cho")
        text = open(path).read()
        decl = re.search(r"^module\s+(\w+)\s*;", text, re.M)
        if not decl or decl.group(1) != m:
            raise SystemExit(f"{path} does not declare module {m}")
        imports = [i for i in re.findall(r"^import\s+([\w.]+)\s*;", text, re.M) if not i.startswith("std.")]
        problems += interface_problems(text, m, imports)
        found[m] = (path, imports)
    if problems:
        raise SystemExit("the published set has a module that is not a stable interface:\n" + "\n".join("  " + p for p in problems))
    return found


def order(found):
    """MODULES, each after the modules it imports"""
    done, out = set(), []

    def visit(m, path):
        if m in done:
            return
        if m in path:
            raise SystemExit("import cycle: " + " -> ".join(path + [m]))
        for dep in found[m][1]:
            visit(dep, path + [m])
        done.add(m)
        out.append(m)

    for m in MODULES:
        visit(m, [])
    return out


def publish(src, out):
    exe = compiler(src)
    found = sources(src)
    root = os.path.join(out, STORE)
    shutil.rmtree(root, ignore_errors=True)
    with tempfile.TemporaryDirectory() as locks:
        for m in order(found):
            path, deps = found[m]
            args = [exe, "vcs", "publish", "--store", os.path.join(root, m), "--std"]
            for dep in deps:
                lock = os.path.join(locks, dep + ".lock")
                if not os.path.exists(lock):
                    subprocess.run([exe, "vcs", "lock", "--store", os.path.join(root, dep), "-o", lock, "--all"], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                args += ["--requires", f"{lock}:{os.path.join(root, dep)}"]
            r = subprocess.run(args + [path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if r.returncode != 0:
                raise SystemExit(f"publishing {m} failed:\n{r.stdout}")
            print(f"{m}: {r.stdout.count('published ')} declarations")


def tree(root):
    """{relative path: bytes} of every file under root (a symlink or a special file is read as what it is, so it differs)"""
    files = {}
    for here, dirs, names in os.walk(root):
        for n in dirs + names:
            full = os.path.join(here, n)
            rel = os.path.relpath(full, root)
            if os.path.islink(full) or not (os.path.isdir(full) or os.path.isfile(full)):
                files[rel] = b"<link or special file>"
            elif os.path.isfile(full):
                files[rel] = open(full, "rb").read()
            else:
                files[rel] = None  # a directory (an empty extra one differs too)
    return files


def differences(committed, fresh):
    """what is in one tree only, or has other bytes (never the file times: contents are compared)"""
    a, b = tree(committed), tree(fresh)
    bad = [f"{p} (not in a fresh publish)" for p in sorted(a.keys() - b.keys())]
    bad += [f"{p} (missing)" for p in sorted(b.keys() - a.keys())]
    bad += [f"{p} (differs)" for p in sorted(a.keys() & b.keys()) if a[p] != b[p]]
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=ROOT)
    ap.add_argument("--out", default=ROOT)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if not args.check:
        publish(args.src, args.out)
        return
    committed = os.path.join(args.out, STORE)
    if not os.path.isdir(committed):
        raise SystemExit(committed + " is missing")
    with tempfile.TemporaryDirectory() as scratch:
        publish(args.src, scratch)
        bad = differences(committed, os.path.join(scratch, STORE))
    if bad:
        print("the committed stores are not what a fresh publish gives:")
        print("\n".join("  " + p for p in bad[:20]))
        sys.exit(1)
    print("the committed stores match a fresh publish")


if __name__ == "__main__":
    main()
