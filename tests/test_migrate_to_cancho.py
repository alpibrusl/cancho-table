"""scripts/migrate_to_cancho.py, run on a temporary copy of this checkout (never on the checkout itself).

    python3 -m unittest tests/test_migrate_to_cancho.py -v

Asserts that a dry run changes nothing, that the migration leaves no old name and no `.ls` behind (LICENSE, the
script, this test and the history note excepted), that a second run changes nothing (idempotence), and, when a
`cancho` compiler built at the pinned revision is on PATH (or $CANCHO), that the migrated tree builds and passes
the generated-file gates (schemas, manifest, site, mutants --check). Without that compiler only the build is skipped.
It is outside tests/conformance on purpose: CI's `unittest discover` there runs on the unmigrated tree.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "migrate_to_cancho.py"
OLD = re.compile(r"lex-sys|lex_sys|lexsys|LEX_SYS|LEXSYS|\.ls\b", re.I)
EXCEPT = {"LICENSE", "scripts/migrate_to_cancho.py", "tests/test_migrate_to_cancho.py"}
MARKER = "<!-- cancho-rename-note -->"


def sh(cmd, cwd, **kw):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, **kw)


def tracked(cwd):
    return [p for p in sh(["git", "ls-files", "-z"], cwd).stdout.split("\0") if p]


def snapshot(cwd):
    """Every tracked file's bytes, and the path list: what 'nothing changed' means."""
    return {p: (Path(cwd) / p).read_bytes() for p in tracked(cwd) if (Path(cwd) / p).is_file()}


def compiler():
    exe = os.environ.get("CANCHO") or shutil.which("cancho")
    if not exe:
        return None
    out = sh([exe, "--version"], ".").stdout
    m = re.search(r"rev ([0-9a-f]{40})", out)
    return (exe, m.group(1)) if m else None


class Migration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="migrate-test-")
        cls.work = Path(cls.tmp) / "copy"
        # HEAD of this checkout, with the script as it is on disk (it may not be committed yet).
        sh(["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(cls.work)], ".").check_returncode()
        shutil.copy(SCRIPT, cls.work / "scripts" / "migrate_to_cancho.py")
        if (ROOT / "tests" / "test_migrate_to_cancho.py").exists():
            shutil.copy(__file__, cls.work / "tests" / "test_migrate_to_cancho.py")
        for k, v in (("user.email", "test@example.com"), ("user.name", "test")):
            sh(["git", "config", k, v], cls.work)
        sh(["git", "add", "-A"], cls.work)
        sh(["git", "commit", "-q", "--allow-empty", "-m", "base"], cls.work)
        cls.script = [sys.executable, "scripts/migrate_to_cancho.py"]
        cls.before = snapshot(cls.work)
        cls.dry = sh(cls.script + ["--dry-run"], cls.work)
        cls.after_dry = snapshot(cls.work)
        cls.first = sh(cls.script, cls.work)
        sh(["git", "add", "-A"], cls.work)
        cls.migrated = snapshot(cls.work)
        cls.second = sh(cls.script, cls.work)
        cls.after_second = snapshot(cls.work)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_scripts_succeed(self):
        for r in (self.dry, self.first, self.second):
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_dry_run_changes_nothing(self):
        self.assertEqual(self.before, self.after_dry)
        if "lex-sys.toml" in self.before:
            self.assertIn("would move lex-sys.toml -> cancho.toml", self.dry.stdout)

    def test_no_old_name_is_left(self):
        left = []
        for p, data in self.migrated.items():
            self.assertFalse(p.endswith(".ls"), p)
            self.assertFalse(re.search(r"lex-?sys|lex_sys", p, re.I), p)
            if p in EXCEPT:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for n, line in enumerate(text.split("\n"), 1):
                if OLD.search(line) and MARKER not in line:
                    left.append("%s:%d: %s" % (p, n, line.strip()[:100]))
        self.assertEqual(left, [])
        self.assertIn("cancho.toml", self.migrated)
        self.assertNotIn("lex-sys.toml", self.migrated)

    def test_license_untouched(self):
        self.assertEqual(self.before["LICENSE"], self.migrated["LICENSE"])

    def test_pins(self):
        text = self.migrated["cancho.toml"].decode()
        sys.path.insert(0, str(ROOT / "scripts"))
        import migrate_to_cancho as m
        self.assertRegex(text, r'(?m)^cancho = "%s"' % m.COMPILER_REV)
        revs = re.findall(r'(?m)^rev = "([0-9a-f]+)"', text)
        self.assertTrue(revs)
        self.assertEqual(set(revs), {m.CONTRACT_REV})
        self.assertEqual(len(re.findall(r'(?m)^git = "https://github.com/alpibrusl/cancho-tools"', text)), len(revs))
        self.assertEqual(len(re.findall(r'(?m)^path = "\.cancho-vcs/toolbox\.\w+"', text)), len(revs))

    def test_history_note_once(self):
        self.assertEqual(self.migrated["docs/history.md"].decode().count(MARKER), 1)

    def test_idempotent(self):
        self.assertIn("already migrated", self.second.stdout)
        self.assertEqual(self.migrated, self.after_second)

    def test_the_migrated_tree_builds(self):
        have = compiler()
        if have is None:
            self.skipTest("no cancho compiler on PATH (or $CANCHO): the build is not tried")
        exe, rev = have
        sys.path.insert(0, str(ROOT / "scripts"))
        import migrate_to_cancho as m
        if rev != m.COMPILER_REV:
            self.skipTest("the compiler on PATH is %s, the pin is %s" % (rev[:12], m.COMPILER_REV[:12]))
        env = dict(os.environ, CANCHO=exe)
        steps = [
            self.script + ["--regenerate"],
            [exe, "fmt", "--check", "tools", "generated"],
            [sys.executable, "scripts/schemas.py", "--check"],
            [sys.executable, "scripts/manifest.py", "--check"],
            [sys.executable, "scripts/site.py", "--check"],
        ]
        for m_ in ("select", "filter", "parallel", "cellcost", "sort", "numbers"):
            if (self.work / "scripts" / ("%s_mutants.py" % m_)).exists():
                steps.append([sys.executable, "scripts/%s_mutants.py" % m_, "--check"])
        for cmd in steps:
            r = sh(cmd, self.work, env=env)
            self.assertEqual(r.returncode, 0, " ".join(cmd) + "\n" + r.stdout[-2000:] + r.stderr[-2000:])
        self.assertTrue((self.work / "build" / "table").exists())
        # What --regenerate wrote is only the compiler pin; the second pass over the tree is still a no-op.
        r = sh(self.script, self.work, env=env)
        self.assertIn("already migrated", r.stdout)


if __name__ == "__main__":
    unittest.main()
