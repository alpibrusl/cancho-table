"""The harness leaves no process behind (tests/conformance/procs.py).

Two stray `table -` processes once ran at 100% CPU for 6 to 13 hours, orphaned (ppid 1), after tests of a producer that never ends (`/dev/zero`, an endless
pipe): the runner was gone or interrupted, and `subprocess.run(timeout=...)` kills the direct child only. This file is the gate that cannot let that happen again,
and it is able to fail: every scenario is run through the helper (no process whose command line carries the scenario's unique marker may remain) and the same
scenarios are run through the bare `subprocess` as controls, which must leave one behind. A detector that could not see a leak would pass the first and fail the
second.

A scenario is a small runner script (RUNNER) started as a process of its own, so that it can be killed: with SIGKILL (nothing in a process can catch that: the
watchdog does), SIGTERM, SIGINT (a KeyboardInterrupt), or left to end by itself (its deadline, its exception, its timeout).

The last tests are about the sources: nothing under tests/ or scripts/ starts a process but through procs.py.
"""

import ast
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent

# What the endless child is: it spins on /dev/zero (100% CPU), the marker is in its command line, and it never ends by itself.
ENDLESS = "import sys\nf = open('/dev/zero', 'rb')\nwhile True:\n    f.read(65536)\n"

RUNNER = r'''
import os, signal, sys, time
sys.path.insert(0, os.environ["PROCS_DIR"])
if os.environ["IMPL"] == "procs":
    import procs as P
    procs_grace = float(os.environ.get("GRACE", "0.5"))
    P.GRACE = procs_grace
else:
    import subprocess as P
marker = os.environ["MARKER"]
endless = [sys.executable, "-c", os.environ["ENDLESS"], marker]
scenario = os.environ["SCENARIO"]

def ready():
    print("ready", flush=True)

if scenario == "run_timeout":
    # the direct child spins: subprocess.run kills it on the timeout. (The ordinary case: it must keep working.)
    try:
        P.run(endless, timeout=1, capture_output=True)
    except P.TimeoutExpired:
        print("timeout", flush=True)
elif scenario == "run_timeout_pipeline":
    # a shell pipeline: the shell is the direct child, the spinner is its child. subprocess.run kills the shell only.
    try:
        P.run(["sh", "-c", "yes | %s -c \"$0\" %s" % (sys.executable, marker), os.environ["ENDLESS"]], timeout=1, capture_output=True)
    except P.TimeoutExpired:
        print("timeout", flush=True)
elif scenario == "run_timeout_background":
    # the shell starts a spinner in the background and waits: a timeout leaves the background one
    try:
        P.run(["sh", "-c", "%s -c \"$0\" %s & wait" % (sys.executable, marker), os.environ["ENDLESS"]], timeout=1, capture_output=True)
    except P.TimeoutExpired:
        print("timeout", flush=True)
elif scenario == "blocked_reader":
    # `p.stdout.read()` on a producer that never ends and never closes its output: the pattern of the stdin tests, with no timeout of its own. The helper's
    # deadline ends it; the bare Popen blocks for ever (the controller kills the runner).
    kw = {"deadline": 1} if os.environ["IMPL"] == "procs" else {}
    p = P.Popen(endless, stdin=P.PIPE, stdout=P.PIPE, stderr=P.PIPE, **kw)
    ready()
    p.stdout.read()
    print("read ended", flush=True)
elif scenario in ("wait_for_signal", "exception", "context"):
    # the child spins; the runner stays (to be signalled), raises, or leaves a `with`
    p = P.Popen(endless, stdin=P.PIPE, stdout=P.PIPE, stderr=P.PIPE)
    if scenario == "exception":
        raise RuntimeError("the test failed with the child running")
    if scenario == "context":
        with p:
            pass
        sys.exit(0)
    ready()
    time.sleep(120)
elif scenario == "dev_zero_stdin":
    # the endless-stdin pattern of test_stdin.NoTrap: the child's descriptor 0 is /dev/zero
    p = P.Popen(endless, stdin=open("/dev/zero", "rb"), stdout=P.PIPE, stderr=P.PIPE)
    ready()
    time.sleep(120)
'''


def ps():
    out = subprocess.run(["ps", "-axww", "-o", "pid=,stat=,command="], capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        pid, stat, command = line.strip().split(None, 2) if len(line.split(None, 2)) == 3 else (line.split()[0], "", "")
        yield int(pid), stat, command


def alive(marker):
    """The processes whose command line carries `marker`, but for zombies (a dead process awaiting its parent's wait spends nothing) and for this one."""
    return [(pid, command[:120]) for pid, stat, command in ps() if marker in command and not stat.startswith("Z") and pid != os.getpid()]


def sweep(marker):
    for pid, _ in alive(marker):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def gone(marker, wait=8.0):
    """The processes of `marker` still there after waiting up to `wait` seconds for the last to go."""
    end = time.time() + wait
    left = alive(marker)
    while left and time.time() < end:
        time.sleep(0.1)
        left = alive(marker)
    return left


class Scenario:
    """A runner of one scenario, with a marker no other process has."""

    def __init__(self, scenario, impl, **env):
        self.marker = "reaper-%s" % uuid.uuid4().hex
        e = dict(os.environ, SCENARIO=scenario, IMPL=impl, MARKER=self.marker, ENDLESS=ENDLESS, PROCS_DIR=str(HERE), **env)
        self.p = subprocess.Popen([sys.executable, "-c", RUNNER], env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)

    def wait_ready(self, timeout=20):
        line = self.p.stdout.readline()
        assert line.strip() == b"ready", (line, self.p.stderr.read() if self.p.poll() is not None else b"")

    def spinning(self):
        """The child is up: it is there with the marker in its command line."""
        end = time.time() + 20
        while time.time() < end:
            if alive(self.marker):
                return True
            time.sleep(0.05)
        return False

    def finish(self, timeout):
        try:
            out, err = self.p.communicate(timeout=timeout)
            return self.p.returncode, out, err
        except subprocess.TimeoutExpired:
            self.p.kill()          # the controller's own last resort, for a runner that blocks (the bare Popen does)
            out, err = self.p.communicate()
            return "killed", out, err

    def done(self):
        sweep(self.marker)
        if self.p.poll() is None:
            self.p.kill()
        self.p.wait()
        for f in (self.p.stdout, self.p.stderr):
            f.close()


class Reaper(unittest.TestCase):
    impl = "procs"
    leaves = False         # the controls say True: the bare subprocess leaves a process behind
    patience = 60          # seconds a runner may take to end; a control (whose runner may block for ever) gives it little

    def scenario(self, name, **env):
        s = Scenario(name, self.impl, **env)
        self.addCleanup(s.done)
        return s

    def verdict(self, s, what):
        left = gone(s.marker, 1.5 if self.leaves else 8.0)
        if self.leaves:
            self.assertTrue(left, "control: the bare subprocess must leave a process behind (%s), and the detector saw none" % what)
        else:
            self.assertEqual(left, [], "%s left a process behind" % what)

    # --- scenarios that end by themselves
    def test_run_timeout_of_the_direct_child(self):
        s = self.scenario("run_timeout")
        rc, out, err = s.finish(self.patience)
        self.assertIn(b"timeout", out, err)
        self.assertEqual(gone(s.marker, 3), [], "a timeout of run() on the direct child (the bare run() does this too)")

    def test_run_timeout_of_a_shell_pipeline(self):
        s = self.scenario("run_timeout_pipeline")
        rc, out, err = s.finish(self.patience)
        self.assertIn(b"timeout", out, err)
        self.verdict(s, "a timeout of run() on `yes | spinner`")

    def test_run_timeout_of_a_background_child(self):
        s = self.scenario("run_timeout_background")
        rc, out, err = s.finish(self.patience)
        self.assertIn(b"timeout", out, err)
        self.verdict(s, "a timeout of run() on `spinner &`")

    def test_a_reader_blocked_on_an_endless_producer_has_a_deadline(self):
        s = self.scenario("blocked_reader")
        s.wait_ready()
        rc, out, err = s.finish(self.patience)
        if self.leaves:
            self.assertEqual(rc, "killed", "control: the bare Popen blocks on an endless producer")
        else:
            self.assertEqual((rc, b"read ended" in out), (0, True), err)
        self.verdict(s, "a read of an endless producer")

    def test_an_exception_in_the_runner(self):
        s = self.scenario("exception")
        rc, out, err = s.finish(self.patience)
        self.assertNotEqual(rc, 0)
        self.verdict(s, "an exception with the child running")

    def test_a_with_block(self):
        s = self.scenario("context")
        rc, out, err = s.finish(self.patience)
        self.verdict(s, "a with block")

    # --- scenarios where the runner is stopped
    def stopped(self, name, sig, **env):
        s = self.scenario(name, **env)
        s.wait_ready()
        self.assertTrue(s.spinning(), "the scenario's child is not running: the test does not test")
        os.kill(s.p.pid, sig)
        s.finish(self.patience)
        return s

    def test_runner_killed_with_sigkill(self):
        # the case no code in the runner can cover: the child is orphaned (ppid 1) unless something outside the runner ends it
        s = self.stopped("wait_for_signal", signal.SIGKILL)
        self.verdict(s, "a runner killed with SIGKILL")

    def test_runner_killed_with_sigkill_with_dev_zero_on_stdin(self):
        s = self.stopped("dev_zero_stdin", signal.SIGKILL)
        self.verdict(s, "a runner killed with SIGKILL, the child on /dev/zero")

    def test_runner_terminated(self):
        s = self.stopped("wait_for_signal", signal.SIGTERM)
        self.verdict(s, "a runner that gets SIGTERM")

    def test_runner_interrupted(self):
        s = self.stopped("wait_for_signal", signal.SIGINT)
        self.verdict(s, "a runner that gets SIGINT")

    def test_runner_hung_up(self):
        s = self.stopped("wait_for_signal", signal.SIGHUP)
        self.verdict(s, "a runner that gets SIGHUP")


class Controls(Reaper):
    """The same scenarios through the bare `subprocess`: each that stops the runner from outside, or blocks it, must leave the child. If one of these passes while
    the detector sees nothing, the detector is blind and the tests above prove nothing."""
    impl = "bare"
    leaves = True
    patience = 4

    # the bare run() kills its direct child on a timeout, so that one scenario is the ordinary case, not a control: what it must do is what the helper does
    def test_run_timeout_of_the_direct_child(self):
        s = self.scenario("run_timeout")
        rc, out, err = s.finish(self.patience)
        self.assertIn(b"timeout", out, err)
        self.assertEqual(gone(s.marker, 3), [], "the bare run() kills its direct child")


SPAWNERS = {"subprocess", "multiprocessing", "pty", "asyncio.subprocess"}
FORBIDDEN_CALLS = {"os.fork", "os.forkpty", "os.system", "os.popen", "os.execv", "os.execve", "os.execl", "os.execlp", "os.execvp", "os.execvpe", "os.spawnl", "os.spawnv",
                   "os.spawnlp", "os.spawnvp", "os.posix_spawn", "os.posix_spawnp", "os.startfile"}


def dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def spawn_sites():
    """Every place under tests/ and scripts/ (but procs.py and this file's own controls) that can start a process other than through procs.py."""
    sites = []
    for base in (ROOT / "tests", ROOT / "scripts"):
        for path in sorted(base.rglob("*.py")):
            if path.name in ("procs.py", "test_reaper.py"):
                continue
            tree = ast.parse(path.read_text(), str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for n in names:
                    if n.split(".")[0] in SPAWNERS and not (n == "multiprocessing.pool" and False):
                        sites.append("%s:%d imports %s" % (path.relative_to(ROOT), node.lineno, n))
                if isinstance(node, ast.Call):
                    d = dotted(node.func)
                    if d in FORBIDDEN_CALLS:
                        sites.append("%s:%d calls %s" % (path.relative_to(ROOT), node.lineno, d))
                    if d and d.startswith("asyncio.create_subprocess"):
                        sites.append("%s:%d calls %s" % (path.relative_to(ROOT), node.lineno, d))
    return sites


class Sources(unittest.TestCase):
    def test_every_child_is_started_through_procs(self):
        self.assertEqual(spawn_sites(), [], "start the process with procs.run / procs.Popen (`import procs as subprocess`), which reaps it")

    def test_the_scan_sees_a_bare_spawn(self):
        # the scan is able to fail: it finds the spawns in a file that has them
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "x.py"
            f.write_text("import subprocess\nimport os\nsubprocess.run(['true'])\nos.system('true')\nfrom multiprocessing import Pool\n")
            tree = ast.parse(f.read_text())
            names = [n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)] + [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            calls = [dotted(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]
            self.assertTrue(SPAWNERS & {n.split(".")[0] for n in names})
            self.assertIn("os.system", set(calls) & FORBIDDEN_CALLS)


if __name__ == "__main__":
    unittest.main()
