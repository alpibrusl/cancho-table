# The demo video

About 126 seconds, no audio: nine short scenes of `table` and a closing card (the card is held for 4 s). The terminal is 80x22 and the text is drawn at 24 px (cells of about 14x30 px), so it fills most of the 1280x720 frame; no caption, command or output line is 80 characters or longer, so nothing wraps (a long command is typed on purpose with backslash continuations at flag boundaries; `test_demo.py` checks it). Files in `docs/assets/demo/`: `table-demo.cast` (the recording), `table-demo.mp4`, `table-demo.webm`, `table-demo.gif` and `table-demo-poster.png`, all 1280x720.

**The typing is simulated; the output is real.** Each character of a caption or a command is written with a small delay, and the prompt is printed by the script. Every command is then run for real, with the binary built from the commit below, on small files the script generates in a temporary directory. What the terminal shows is what the tool printed. Nothing is edited afterwards, there is no speed claim and no timing on screen. **The cast is the source of truth:** the GIF, MP4, WEBM and poster are drawn from it by `render.sh` and change nothing in it. Three scenes pipe the tool's output into `jq` or `shasum` to show a part of it (the rule, hint and repair of the refusal; the file size and the bytes read by `--explain`) or its hash; the pipe is on screen and the caption says what is filtered.

Recorded at commit `0a1b7dc614e401e4c2e91ef7a17c84a70a2c740a` (origin/main; `record.sh` writes the merge-base with origin/main), compiler `e47d647` (the pin in `cancho.toml`); the cast's title and its `TABLE_COMMIT` env entry say the same.

## What is here

| file | what |
|---|---|
| `storyboard.py` | the one list of scenes, captions and commands, the files they run on, and the exit status each must end with |
| `demo.py` | plays the storyboard with simulated typing and real runs (`--fast`: no delays) |
| `record.sh` | runs `demo.py` under `asciinema rec`, 80x22, idle capped at 5 s; each output stays 2.8 s, the key ones (the repair, the exact decimals, the identical hashes) 4 to 4.5 s |
| `render.sh` | cast to GIF (`agg`), MP4 (H.264, yuv420p, faststart), WEBM (VP9) and poster (`ffmpeg`, under `nice`) |
| `expected.txt` | the golden transcript: every command, its output and its exit status |

## Regenerate

```sh
cancho build                                  # build/table, with the pinned compiler (README quick start)
python3 scripts/demo/storyboard.py --write    # only if the tool's output changed on purpose: rewrites expected.txt, review the diff
sh scripts/demo/record.sh                     # docs/assets/demo/table-demo.cast
sh scripts/demo/render.sh                     # gif, mp4, webm, poster next to it
```

Needs `asciinema`, `agg`, `ffmpeg`, `python3`, `jq`, `shasum`. `render.sh` reads `FONT_SIZE`, `FPS`, `COLORS`, `CRF`, `WCRF` and `POSTER_AT` from the environment. After a new recording, update the commit above.

## The gate

`tests/conformance/test_demo.py` runs every command of the storyboard against the current build and fails if a command ends with another exit status than the storyboard says (a refusal nobody expected, or one that stopped happening) or if the transcript differs from `expected.txt` by a byte. So the demo cannot say something the tool no longer says: when the tool changes, the gate fails until `expected.txt` is rewritten and the video recorded again. The test also mutates an expectation, an exit status and an output, and requires each to be noticed.

## Embedding

The MP4 with a poster, and WEBM as a second source, with a link for browsers that cannot play video (works without JavaScript):

```html
<video controls muted playsinline preload="none" poster="assets/demo/table-demo-poster.png" width="1280" height="720" style="max-width:100%;height:auto">
  <source src="assets/demo/table-demo.mp4" type="video/mp4">
  <source src="assets/demo/table-demo.webm" type="video/webm">
  <a href="assets/demo/table-demo.mp4">Watch the demo (MP4)</a>
</video>
```

No-JS and no-video fallback, the GIF:

```html
<noscript><img src="assets/demo/table-demo.gif" alt="A terminal session: a question answered exactly, a refusal with its repair, exact decimals, a plan, JSON lines, a join, a query, the same bytes on 1 and 4 cores, and a pipe." width="1280" height="720" style="max-width:100%;height:auto"></noscript>
```

The cast, with the small asciinema player (a text file, about 50 KB; copy the player's two files from a release into `docs/assets/` first, or load them from a CDN; pin the version):

```html
<link rel="stylesheet" href="assets/asciinema-player.min.css">
<div id="demo"></div>
<script src="assets/asciinema-player.min.js"></script>
<script>AsciinemaPlayer.create('assets/demo/table-demo.cast', document.getElementById('demo'), {idleTimeLimit: 5, poster: 'npt:0:9', fit: 'width'});</script>
```

## Notes on rendering

* The three videos have the same length (the last frame, the closing card, is held `--last-frame-duration 4` s in each); `render.sh` fails if they differ by 0.2 s or more. In the first render the MP4's last packet lasted 0.1 s instead of 4 s (93.2 s against 96.6 s for the GIF and WEBM); the cause was not isolated, and the same ffmpeg command on the new cast keeps the hold.
* The videos are variable-frame-rate: a frame can last 4 s. Seeking with `ffmpeg -ss T -i table-demo.mp4` returns the first frame at or after T, which on a long hold is the next scene's first frame, not what was on screen. To look at the end of a scene, extract at a fixed rate (`-vf fps=4`) and take the frame at that second.
