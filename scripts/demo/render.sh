#!/bin/sh
# Render the cast into the GIF, MP4 (H.264, yuv420p, faststart), WEBM (VP9) and poster PNG, all 1280x720.
#
#   sh scripts/demo/render.sh [CAST [OUTDIR]]    default: docs/assets/demo/table-demo.cast, same directory
#
# Needs agg and ffmpeg. The cast is the source of truth: nothing here edits what it shows, it only draws it. Idle time is capped at 1.2 s again
# (agg), the last frame is held 4 s (the closing card), and ffmpeg runs under nice.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
cast=${1:-$root/docs/assets/demo/table-demo.cast}
dir=${2:-$(dirname "$cast")}
name=table-demo
for t in agg ffmpeg; do command -v "$t" >/dev/null || { echo "missing: $t" >&2; exit 2; }; done
mkdir -p "$dir"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# 1. cast -> gif at the terminal's natural size; the theme is the project page's terminal (#0d1117 on #d6dde8)
nice agg -q --theme 0d1117,d6dde8,0d1117,e5534b,57ab5a,c69026,539bf5,b083f0,39c5cf,adbac7,545d68,ff938a,6bc46d,daaa3f,6cb6ff,dcbdfb,56d4dd,cdd9e5 \
  --font-size "${FONT_SIZE:-19}" --line-height 1.25 --idle-time-limit 1.2 --last-frame-duration 4 --fps-cap "${FPS:-12}" --no-loop \
  "$cast" "$tmp/natural.gif"

# 2. every output is 1280x720: the terminal is centred on the same background
bg=0d1117
vf="scale=1280:720:force_original_aspect_ratio=decrease:flags=lanczos,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=$bg"
nice ffmpeg -y -loglevel error -i "$tmp/natural.gif" -vf "$vf,split[a][b];[a]palettegen=max_colors=${COLORS:-48}:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" \
  -gifflags +transdiff "$dir/$name.gif"
nice ffmpeg -y -loglevel error -i "$tmp/natural.gif" -vf "$vf,format=yuv420p" -c:v libx264 -preset slow -crf "${CRF:-23}" -movflags +faststart -an "$dir/$name.mp4"
nice ffmpeg -y -loglevel error -i "$tmp/natural.gif" -vf "$vf,format=yuv420p" -c:v libvpx-vp9 -b:v 0 -crf "${WCRF:-34}" -row-mt 1 -an "$dir/$name.webm"

# 3. the poster: the frame where the first answer is on screen
nice ffmpeg -y -loglevel error -ss "${POSTER_AT:-8.5}" -i "$dir/$name.mp4" -frames:v 1 "$dir/$name-poster.png"
ls -l "$dir"/$name*
