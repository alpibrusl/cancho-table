"""Play the storyboard in a terminal: simulated typing, real runs.

What is simulated is the typing: each character of a caption or a command is written with a delay, as a person would type it, and the prompt is printed here, not by a shell.
What is not: every command is run for real, with the built binary, in a scratch directory that holds the files storyboard.make_files writes, and what it prints is what
the terminal shows. record.sh runs this under `asciinema rec`; the cast it writes is the source of truth of the video.

  python3 scripts/demo/demo.py            play at the speed of the video
  python3 scripts/demo/demo.py --fast     no delays (to look at it)

TABLE_BIN is the binary (default: build/table of this checkout).
"""

import os
import random
import sys
import tempfile
import time

import storyboard
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py (storyboard put it on the path)

FAST = "--fast" in sys.argv
CHAR = 0.0 if FAST else 0.018  # seconds per character typed (mean)
PAUSE_BEFORE_RUN = 0.0 if FAST else 0.5
PAUSE_AFTER_OUTPUT = 0.0 if FAST else 1.2  # the idle time between commands is capped at this, as render.sh also caps it
PAUSE_AFTER_CAPTION = 0.0 if FAST else 0.5
rng = random.Random(7)

RESET = "\x1b[0m"
PROMPT = "\x1b[1;32m$\x1b[0m "
CAPTION = "\x1b[38;5;110m"  # a comment: the blue of the site's terminals
BOLD = "\x1b[1;97m"
URL = "\x1b[4;38;5;114m"


def out(s):
    sys.stdout.write(s)
    sys.stdout.flush()


def pause(t):
    if t > 0:
        time.sleep(t)


def type_text(s):
    for ch in s:
        out(ch)
        if CHAR:
            pause(CHAR * rng.uniform(0.5, 1.6) * (3.0 if ch == " " and rng.random() < 0.15 else 1.0))


def caption(line):
    out(CAPTION)
    type_text("# " + line)
    out(RESET + "\n")


def clear_screen():
    out(PROMPT)
    type_text("clear")
    out("\n")
    pause(0.3 if not FAST else 0)
    out("\x1b[2J\x1b[H")


def run(cmd, env, cwd):
    out(PROMPT)
    type_text(cmd)
    out("\n")
    pause(PAUSE_BEFORE_RUN)
    sys.stdout.flush()
    subprocess.run(storyboard.line_of(cmd), cwd=cwd, env=env, timeout=120)  # stdout and stderr are the terminal's: the output is the tool's own
    pause(PAUSE_AFTER_OUTPUT)


def closing():
    out("\n" * 8)
    width = 100
    for i, line in enumerate(storyboard.CLOSING):
        style = URL if line.startswith("http") else BOLD
        out(" " * ((width - len(line)) // 2) + style)
        type_text(line)
        out(RESET + "\n")
        pause(0.3 if not FAST else 0)
    pause(0.4 if not FAST else 0)


def main():
    table = os.environ.get("TABLE_BIN") or str(storyboard.ROOT / "build" / "table")
    with tempfile.TemporaryDirectory() as d:
        storyboard.make_files(d)
        env = storyboard.environment(table, d)
        out("\x1b[2J\x1b[H")
        pause(0.6 if not FAST else 0)
        for n, (_, lines, cmds) in enumerate(storyboard.SCENES):
            if n:
                clear_screen()
            for line in lines:
                caption(line)
            pause(PAUSE_AFTER_CAPTION)
            for c in cmds:
                run(c["cmd"], env, d)
        clear_screen()
        closing()


if __name__ == "__main__":
    main()
