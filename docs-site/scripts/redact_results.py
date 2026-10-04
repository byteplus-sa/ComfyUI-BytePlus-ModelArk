#!/usr/bin/env python3
"""
Mask account-specific IDs (asset IDs, group IDs, task IDs) in the result images of the live run
and collapse empty video areas. Run after scripts/import_evidence.py (which overwrites the images).

  ~/ComfyUI-Installs/ComfyUI/ComfyUI/.venv/bin/python scripts/redact_results.py

Each rule: image slug, box (x0, y0, x1, y1) of the line to replace, text to draw instead.
"""
import io
import os
import subprocess

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "src", "assets", "templates")
FONT = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 19)

RULES = {
    "video-and-audio-assets": [
        *[((24, y, 380, y + 24), "asset://asset-<id>") for y in (100, 124, 148, 172)],
        ((34, 572, 500, 596), '"asset_id": "asset-<id>",'),
        ((34, 596, 500, 620), '"asset_uri": "asset://asset-<id>",'),
        ((34, 644, 500, 668), '"group_id": "group-<id>",'),
        ((34, 834, 500, 858), '"asset_id": "asset-<id>",'),
        ((34, 858, 500, 882), '"asset_uri": "asset://asset-<id>",'),
        ((34, 906, 500, 930), '"group_id": "group-<id>",'),
    ],
    "virtual-portrait-new-asset": [
        ((34, 429, 500, 453), '"asset_id": "asset-<id>",'),
        ((34, 453, 500, 477), '"asset_uri": "asset://asset-<id>",'),
        ((34, 501, 500, 525), '"group_id": "group-<id>",'),
    ],
    "consistent-character-shots": [((24, 405, 380, 429), "asset://asset-<id>")],
    "video-smoothness-enhance": [((252, 710, 724, 736), '"amk-tool-enhance-video-smoothness-<id>"')],
}


COLLAPSE = {"vcube-video-enhance", "video-smoothness-enhance", "virtual-portrait-new-asset", "seedance-video-extension"}


def collapse_empty(im, min_run=90, keep=14):
    """Cut runs of empty (single colour) rows, such as video frames that did not render."""
    px = im.load()
    w, h = im.size
    bg = px[w - 3, h // 2]
    empty = [all(px[x, y] == bg for x in range(0, w, 7)) for y in range(h)]
    rows, y = [], 0
    while y < h:
        if empty[y]:
            end = y
            while end < h and empty[end]:
                end += 1
            if end - y >= min_run:
                rows.extend(range(y, y + keep))
            else:
                rows.extend(range(y, end))
            y = end
        else:
            rows.append(y)
            y += 1
    out = Image.new("RGB", (w, len(rows)))
    for i, src in enumerate(rows):
        out.paste(im.crop((0, src, w, src + 1)), (0, i))
    return out


def add_frames(im, slug, count=3, width=440):
    """Append frames of the sample clip (public/media/<slug>.mp4) where the run's panel had none to show."""
    clip = os.path.join(os.path.dirname(ASSETS), "..", "..", "public", "media", f"{slug}.mp4")
    clip = os.path.normpath(clip)
    if not os.path.exists(clip):
        return im
    duration = float(subprocess.run(["/opt/homebrew/bin/ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", clip],
                                    capture_output=True, text=True).stdout.strip() or 0)
    frames = []
    for i in range(count):
        t = duration * (0.08 + 0.84 * i / (count - 1))
        raw = subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", clip, "-frames:v", "1", "-vf", f"scale={width}:-2",
                              "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
        if raw:
            frames.append(Image.open(io.BytesIO(raw)).convert("RGB"))
    if not frames:
        return im
    bg = im.getpixel((im.width - 3, im.height - 3))
    height = max(f.height for f in frames)
    out = Image.new("RGB", (im.width, im.height + height + 24), bg)
    out.paste(im, (0, 0))
    x = 24
    for f in frames:
        out.paste(f, (x, im.height + 8))
        x += f.width + 12
    return out


FRAMES = {"vcube-video-enhance", "video-smoothness-enhance", "virtual-portrait-new-asset", "seedance-video-extension"}

for slug in sorted(set(RULES) | COLLAPSE):
    path = os.path.join(ASSETS, f"{slug}-result.webp")
    im = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(im)
    for (x0, y0, x1, y1), text in RULES.get(slug, []):
        draw.rectangle((x0, y0, x1, y1), fill=im.getpixel((x1 + 4, y0 + 2)))
        draw.text((x0, y0 + 1), text, font=FONT, fill=(255, 255, 255))
    if slug in COLLAPSE:
        im = collapse_empty(im)
    if slug in FRAMES:
        im = add_frames(im, slug)
    im.save(path, "WEBP", quality=86, method=6)
    print(slug, im.size)
