#!/usr/bin/env python3
"""Render the kiosk attract loop (asset/kiosk/) to a standalone 1080p mp4.

Drives index.html in record mode with Playwright, stepping window.kiosk.render(t)
one frame at a time and piping JPEG screenshots straight into ffmpeg (H.264,
yuv420p, silent AAC track so picky signage players accept it, faststart).
Deterministic: the page animates nothing on its own in record mode.

Usage (from the repo root, with `make local` up; Playwright + Chromium installed
for the system python3):
  python3 scripts/render_kiosk_video.py
      [--url http://localhost:8888/themes/sarkin-jeppesen/asset/kiosk/index.html]
      [--out ~/wiki/collateral/aips-tokyo-kiosk/aips-tokyo-kiosk-1080p.mp4]
      [--fps 30] [--all] [--crf 18]

`--all` renders the whole deck; the default is the slides tagged `video` in
scripts/kiosk/slides.txt (about four minutes).
"""
from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_URL = "http://localhost:8888/themes/sarkin-jeppesen/asset/kiosk/index.html"
DEFAULT_OUT = Path("~/wiki/collateral/aips-tokyo-kiosk/aips-tokyo-kiosk-1080p.mp4").expanduser()
POSTER_AT_MS = 15000   # first slide fully in, caption up


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", default=DEFAULT_OUT, type=Path)
    ap.add_argument("--fps", default=30, type=int)
    ap.add_argument("--crf", default=18, type=int)
    ap.add_argument("--all", action="store_true", help="whole deck, not just `video` slides")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found on PATH")
    from playwright.sync_api import sync_playwright

    args.out.parent.mkdir(parents=True, exist_ok=True)
    poster = args.out.with_name("poster.jpg")
    url = args.url + ("?record=1" if args.all else "?record=1&video=1")

    ffmpeg = subprocess.Popen([
        "ffmpeg", "-y", "-loglevel", "error", "-stats",
        "-f", "image2pipe", "-framerate", str(args.fps), "-c:v", "mjpeg", "-i", "-",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-shortest",
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.1", "-preset", "slow",
        "-crf", str(args.crf), "-pix_fmt", "yuv420p", "-r", str(args.fps),
        "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart",
        str(args.out),
    ], stdin=subprocess.PIPE)
    assert ffmpeg.stdin is not None

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page.goto(url, wait_until="load")
        page.wait_for_function("window.kiosk && window.kiosk.ready === true", timeout=180000)
        total_ms = page.evaluate("window.kiosk.totalMs")
        n_slides = page.evaluate("window.kiosk.slides.length")
        n_frames = math.ceil(total_ms / 1000 * args.fps)
        print(f"{n_slides} slides, {total_ms / 1000:.0f}s, {n_frames} frames at {args.fps} fps")

        started = time.time()
        for i in range(n_frames):
            t = i * 1000 / args.fps
            page.evaluate("t => window.kiosk.render(t)", t)
            frame = page.screenshot(type="jpeg", quality=92)
            ffmpeg.stdin.write(frame)
            if i == round(POSTER_AT_MS / 1000 * args.fps):
                poster.write_bytes(frame)
            if i % (args.fps * 10) == 0 and i:
                rate = i / (time.time() - started)
                print(f"  frame {i}/{n_frames}  ({rate:.1f} fps, ~{(n_frames - i) / rate / 60:.1f} min left)")
        browser.close()

    ffmpeg.stdin.close()
    if ffmpeg.wait() != 0:
        sys.exit("ffmpeg failed")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.0f} MB) and {poster.name}")


if __name__ == "__main__":
    main()
