#!/usr/bin/env python3
"""
Mask account-specific IDs (asset IDs, group IDs, task IDs) in the result images of the live run
and collapse empty video areas. Run after scripts/import_evidence.py (which overwrites the images).

  ~/ComfyUI-Installs/ComfyUI/ComfyUI/.venv/bin/python scripts/redact_results.py

Each rule: image slug, box (x0, y0, x1, y1) of the line to replace, text to draw instead.
"""
import os

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


for slug in sorted(set(RULES) | {"vcube-video-enhance", "video-smoothness-enhance", "virtual-portrait-new-asset"}):
    path = os.path.join(ASSETS, f"{slug}-result.webp")
    im = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(im)
    for (x0, y0, x1, y1), text in RULES.get(slug, []):
        draw.rectangle((x0, y0, x1, y1), fill=im.getpixel((x1 + 4, y0 + 2)))
        draw.text((x0, y0 + 1), text, font=FONT, fill=(255, 255, 255))
    if slug in ("vcube-video-enhance", "video-smoothness-enhance", "virtual-portrait-new-asset"):
        im = collapse_empty(im)
    im.save(path, "WEBP", quality=86, method=6)
    print(slug, im.size)
