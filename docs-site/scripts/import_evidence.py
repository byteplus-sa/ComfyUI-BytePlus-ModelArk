#!/usr/bin/env python3
"""
Copy the result images and sample media of a live end-to-end run into the docs, and write
src/data/evidence.json (which case ran each template, how long it took).

  ~/ComfyUI-Installs/ComfyUI/ComfyUI/.venv/bin/python scripts/import_evidence.py /path/to/e2e-report/2026-10-03

The report folder is not part of the repository (it is git-ignored); only the converted images
and short clips are committed. Needs Pillow and ffmpeg.
"""
import json
import os
import subprocess
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
FFMPEG = "/opt/homebrew/bin/ffmpeg"
TEST_DATE = "2026-10-03"

# template title -> case that is shown on its page
PRIMARY = {
    "Seedream": "tpl-13-seedream", "Seedance 2": "tpl-10-seedance-2", "Seed": "tpl-08-seed",
    "Text to Image to Video": "tpl-14-text-to-image-to-video",
    "Seedream Layer Separation": "tpl-12-layer-separation", "Image Quality Enhance": "tpl-03-image-quality-enhance",
    "Generate and Enhance": "tpl-02-generate-enhance", "2.5 Model Updates": "tpl-01-model-updates",
    "Seedance Video Extension": "tpl-11-video-extension", "Seedance Task Query": "cov-query",
    "Image to Draft to 1080p": "image-reference-draft-final",
    "vCube Video Enhance": "tpl-18-vcube", "Video Smoothness Enhance": "tpl-15-video-smoothness",
    "Seed Prompt Writer": "tpl-05-seed-prompt-writer", "Seed Audio": "tpl-04-seed-audio",
    "Seed Speech TTS and ASR": "tpl-06-seed-speech-tts-asr", "Seed Voice Clone": "tpl-07-seed-voice-clone",
    "Virtual Portrait - Existing Asset": "tpl-16-vp-existing-asset", "Virtual Portrait - New Asset": "tpl-17-vp-new-asset",
    "Video and Audio Assets": "cov-assets", "Image to UGC Video": "show-ugc", "Product Ad in One Click": "show-ad",
    "Old Photo to Living Memory": "show-memory", "Podcast Clip": "show-podcast", "Multilingual Dubbing": "show-dub",
    "Product Lookbook": "show-lookbook", "Sound Design": "show-sound", "Consistent Character Shots": "show-character",
}


# further live runs of the same template (other groups, other options) quoted on its page
EXTRA = {
    "generate-and-enhance": ["tpl-02b-generate-enhance-video"],
    "seedance-2": ["tpl-10b-seedance-2-draft-final", "tpl-10c-seedance-2-flf", "tpl-10d-seedance-2-reference"],
    "virtual-portrait-existing-asset": ["tpl-16b-vp-lookup-by-name"],
}


def to_webp(src, dst, max_width):
    image = Image.open(src).convert("RGB")
    if image.width > max_width:
        image = image.resize((max_width, round(image.height * max_width / image.width)), Image.LANCZOS)
    image.save(dst, "WEBP", quality=86, method=6)


def main(report):
    templates = json.load(open(os.path.join(SITE, "src/data/templates.json")))
    assets = os.path.join(SITE, "src/assets/templates")
    media_dir = os.path.join(SITE, "public/media")
    os.makedirs(media_dir, exist_ok=True)
    evidence = {}
    for t in templates:
        case = PRIMARY[t["title"]]
        folder = os.path.join(report, case)
        result = json.load(open(os.path.join(folder, "result.json")))
        entry = {"case": case, "status": result["status"], "seconds": round(result.get("seconds") or 0),
                 "tested": TEST_DATE, "media": []}
        # The workflow screenshots (<slug>-workflow.webp) are taken from the template files, not from the run:
        # see scripts/capture/README.md. The result image is the run's outputs panel.
        if result["status"] == "success":
            to_webp(os.path.join(folder, "panel.png"), os.path.join(assets, t["slug"] + "-result.webp"), 1800)
            outputs = os.path.join(folder, "outputs")
            names = sorted(os.listdir(outputs)) if os.path.isdir(outputs) else []
            videos = [n for n in names if n.endswith(".mp4")]
            audios = [n for n in names if n.endswith((".mp3", ".wav", ".flac"))]
            if videos:
                dst = os.path.join(media_dir, t["slug"] + ".mp4")
                subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", os.path.join(outputs, videos[-1]), "-vf", "scale=-2:540",
                                "-c:v", "libx264", "-crf", "30", "-preset", "slow", "-c:a", "aac", "-b:a", "64k",
                                "-movflags", "+faststart", dst], check=True)
                entry["media"].append({"type": "video", "path": f"media/{t['slug']}.mp4"})
            for i, name in enumerate(audios[:2]):
                dst = os.path.join(media_dir, f"{t['slug']}-{i}.mp3")
                subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", os.path.join(outputs, name), "-ac", "1", "-b:a", "48k", dst], check=True)
                entry["media"].append({"type": "audio", "path": f"media/{t['slug']}-{i}.mp3"})
        entry["extra_runs"] = []
        for extra in EXTRA.get(t["slug"], []):
            run = json.load(open(os.path.join(report, extra, "result.json")))
            entry["extra_runs"].append({"case": extra, "status": run["status"], "seconds": round(run.get("seconds") or 0)})
        evidence[t["slug"]] = entry
    json.dump(evidence, open(os.path.join(SITE, "src/data/evidence.json"), "w"), indent=1)
    print(len(evidence), "templates;", sum(len(e["media"]) for e in evidence.values()), "media files")


if __name__ == "__main__":
    main(sys.argv[1])
