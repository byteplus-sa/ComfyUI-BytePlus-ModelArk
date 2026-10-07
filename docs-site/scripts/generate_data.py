#!/usr/bin/env python3
"""
Generate the data the docs pages render, so tables never drift from the code.

  python3 scripts/generate_data.py --object-info http://127.0.0.1:8188/object_info

Reads
  - /object_info of a running ComfyUI that has this pack loaded (no API key needed)
  - ../example_workflows/*.json (and the .jpg thumbnails)
  - ../nodes/models_config.py
Writes
  - src/data/nodes.json, templates.json, models.json
  - src/assets/templates/<slug>.jpg (thumbnails), public/workflows/<slug>.json (downloads)

Hand-written text lives in src/content/docs/{nodes,templates}/*.mdx; see CONTRIBUTING-DOCS.md.
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
REPO = os.path.dirname(SITE)

# class -> (slug, group). The order is the sidebar order. Node IDs never change, slugs are the URLs.
NODES = [
    ("BytePlusSeedream", "seedream", "Images"),
    ("BytePlusSeedreamLayerSeparation", "seedream-layer-separation", "Images"),
    ("BytePlusSeedance2TextToVideo", "seedance-2-5-text-to-video", "Video: Seedance 2.5"),
    ("BytePlusSeedance2FirstLastFrame", "seedance-2-5-first-last-frame", "Video: Seedance 2.5"),
    ("BytePlusSeedance2Reference", "seedance-2-5-reference", "Video: Seedance 2.5"),
    ("BytePlusSeedanceDraftToFinal", "seedance-2-5-draft-to-final", "Video: Seedance 2.5"),
    ("BytePlusSeedanceTextToVideo", "seedance-1-text-to-video", "Video: Seedance 1.0"),
    ("BytePlusSeedanceImageToVideo", "seedance-1-image-to-video", "Video: Seedance 1.0"),
    ("BytePlusSeedanceFirstLastFrame", "seedance-1-first-last-frame", "Video: Seedance 1.0"),
    ("BytePlusVideoQueryTasks", "video-query-tasks", "Video tools"),
    ("BytePlusSeed", "byteplus-llm", "Language"),
    ("BytePlusSeedAudio", "seed-audio", "Speech"),
    ("BytePlusSeedTTS", "seed-tts", "Speech"),
    ("BytePlusSeedASR", "seed-asr", "Speech"),
    ("BytePlusSeedVoiceClone", "seed-voice-clone", "Speech"),
    ("BytePlusVideoEnhance", "vcube-video-enhance", "MediaKit"),
    ("BytePlusVideoSmoothness", "video-smoothness-enhance", "MediaKit"),
    ("BytePlusImageEnhance", "image-quality-enhance", "MediaKit"),
    ("BytePlusCreateImageAsset", "create-image-asset", "Assets"),
    ("BytePlusCreateVideoAsset", "create-video-asset", "Assets"),
    ("BytePlusCreateAudioAsset", "create-audio-asset", "Assets"),
    ("BytePlusAssetLibrary", "asset-library", "Assets"),
]
SKIPPED_NODES = {"BytePlusProgressTest"}  # dev-only

# Template title -> group. The order is the sidebar order. A template that is not listed fails the run.
TEMPLATE_GROUPS = [
    ("Start here", ["Seedream", "Seedance 2", "Seed", "Text to Image to Video"]),
    ("Images", ["Seedream Layer Separation", "Image Quality Enhance", "Generate and Enhance"]),
    ("Video", ["Image to Draft to 1080p", "2.5 Model Updates", "Seedance Video Extension", "Seedance Task Query", "vCube Video Enhance", "Video Smoothness Enhance"]),
    ("Language", ["Seed Prompt Writer"]),
    ("Speech", ["Seed Audio", "Seed Speech TTS and ASR", "Seed Voice Clone"]),
    ("Assets", ["Virtual Portrait - Existing Asset", "Virtual Portrait - New Asset", "Video and Audio Assets"]),
    ("Showcase", ["Image to UGC Video", "Product Ad in One Click", "Old Photo to Living Memory", "Podcast Clip",
                  "Multilingual Dubbing", "Product Lookbook", "Sound Design", "Consistent Character Shots"]),
]

# Keys a node needs (Settings > BytePlus). Assets also need the IAM pair.
KEYS_BY_CATEGORY = {
    "BytePlus ModelArk": ["ModelArk API key"],
    "BytePlus ModelArk/Speech": ["Seed Speech API key"],
    "BytePlus ModelArk/MediaKit": ["AI MediaKit API key"],
}
KEYS_BY_CLASS = {
    "BytePlusCreateImageAsset": ["ModelArk API key", "IAM access key and secret key"],
    "BytePlusCreateVideoAsset": ["ModelArk API key", "IAM access key and secret key"],
    "BytePlusCreateAudioAsset": ["ModelArk API key", "IAM access key and secret key"],
    "BytePlusAssetLibrary": ["ModelArk API key", "IAM access key and secret key"],
}

TYPE_LABELS = {
    "STRING": "text", "INT": "integer", "FLOAT": "number", "BOOLEAN": "on/off", "COMBO": "choice",
    "COMFY_DYNAMICCOMBO_V3": "choice with options", "COMFY_AUTOGROW_V3": "group of inputs",
}


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_input(name, spec, required, advanced_default=False):
    """One input -> a flat dict. DynamicCombo and Autogrow carry their children."""
    kind, config = spec[0], (spec[1] if len(spec) > 1 else {}) or {}
    if isinstance(kind, list):  # legacy combo: ["a", "b"]
        kind, config = "COMBO", {**config, "options": kind}
    item = {
        "name": name,
        "type": TYPE_LABELS.get(kind, kind),
        "required": required,
        "advanced": bool(config.get("advanced")),
        "tooltip": config.get("tooltip", ""),
    }
    for key in ("default", "min", "max", "step"):
        if key in config:
            item[key] = config[key]
    if config.get("multiline"):
        item["multiline"] = True
    if kind == "COMBO":
        item["options"] = list(config.get("options", []))
    if kind == "COMFY_DYNAMICCOMBO_V3":
        item["options"] = [o["key"] for o in config["options"]]
        item["variants"] = [
            {"key": o["key"], "inputs": parse_group(o.get("inputs", {}))} for o in config["options"]
        ]
    if kind == "COMFY_AUTOGROW_V3":
        template = config.get("template", {})
        children = parse_group(template.get("input", {}))
        item["item_type"] = children[0]["type"] if children else ""
        item["slots"] = template.get("names") or []
        item["min"] = template.get("min", 0)
    return item


def parse_group(group, order=None):
    items = []
    for section, required in (("required", True), ("optional", False)):
        for name, spec in (group.get(section) or {}).items():
            items.append(parse_input(name, spec, required))
    return items


def build_nodes(object_info):
    nodes = []
    slugs = {cls: (slug, group) for cls, slug, group in NODES}
    for cls, info in object_info.items():
        if not cls.startswith("BytePlus") or cls in SKIPPED_NODES:
            continue
        if cls not in slugs:
            raise SystemExit(f"{cls} has no entry in NODES (scripts/generate_data.py): add a slug and group.")
        slug, group = slugs[cls]
        order = info.get("input_order", {})
        inputs = parse_group(info.get("input", {}))
        position = {n: i for i, n in enumerate(order.get("required", []) + order.get("optional", []))}
        inputs.sort(key=lambda item: position.get(item["name"], 999))
        names, types = info.get("output_name", []), info.get("output", [])
        lists = info.get("output_is_list", [False] * len(names))
        tips = info.get("output_tooltips") or [None] * len(names)
        outputs = [
            {"name": n, "type": TYPE_LABELS.get(t, t), "is_list": bool(lists[i]), "tooltip": tips[i] or ""}
            for i, (n, t) in enumerate(zip(names, types))
        ]
        category = info.get("category", "")
        nodes.append({
            "id": cls,
            "slug": slug,
            "group": group,
            "display_name": info.get("display_name", cls),
            "category": category,
            "description": info.get("description", ""),
            "search_aliases": info.get("search_aliases") or [],
            "keys": KEYS_BY_CLASS.get(cls) or KEYS_BY_CATEGORY.get(category, []),
            "is_output_node": bool(info.get("output_node")),
            "inputs": inputs,
            "outputs": outputs,
        })
    missing = [c for c, _, _ in NODES if c not in {n["id"] for n in nodes}]
    if missing:
        raise SystemExit(f"Registered nodes missing from /object_info: {missing}")
    nodes.sort(key=lambda n: [c for c, _, _ in NODES].index(n["id"]))
    return nodes


def build_templates(nodes, object_info):
    by_id = {n["id"]: n for n in nodes}
    folder = os.path.join(REPO, "example_workflows")
    out_dir = os.path.join(SITE, "src", "assets", "templates")
    os.makedirs(out_dir, exist_ok=True)
    workflow_dir = os.path.join(SITE, "public", "workflows")
    os.makedirs(workflow_dir, exist_ok=True)
    templates = []
    for filename in sorted(f for f in os.listdir(folder) if f.endswith(".json")):
        title = filename[:-5]
        workflow = json.load(open(os.path.join(folder, filename), encoding="utf-8"))
        counts = {}
        for node in workflow.get("nodes", []):
            counts[node["type"]] = counts.get(node["type"], 0) + 1
        byteplus = [t for t in counts if t in by_id]
        keys = []
        for t in byteplus:
            for k in by_id[t]["keys"]:
                if k not in keys:
                    keys.append(k)
        note = ""
        for node in workflow.get("nodes", []):
            if node["type"] in ("MarkdownNote", "Note") and node.get("widgets_values"):
                note = str(node["widgets_values"][0])
        groups = [g.get("title", "") for g in workflow.get("groups", [])]
        slug = slugify(title)
        group = next((g for g, names in TEMPLATE_GROUPS if title in names), None)
        if group is None:
            raise SystemExit(f"Template {title!r} has no group in TEMPLATE_GROUPS (scripts/generate_data.py).")
        shutil.copyfile(os.path.join(folder, filename), os.path.join(workflow_dir, slug + ".json"))
        thumb = os.path.join(folder, title + ".jpg")
        if os.path.exists(thumb):
            shutil.copyfile(thumb, os.path.join(out_dir, slug + ".jpg"))
        templates.append({
            "file": filename,
            "title": title,
            "slug": slug,
            "group": group,
            "nodes": [{"id": t, "count": counts[t]} for t in byteplus],
            "other_nodes": sorted(
                (object_info.get(t, {}).get("display_name") or t) for t in counts if t not in by_id
            ),
            "keys": keys,
            "groups": groups,
            "note": note,
            "has_thumbnail": os.path.exists(thumb),
        })
    order = [t for _, names in TEMPLATE_GROUPS for t in names]
    templates.sort(key=lambda t: order.index(t["title"]))
    return templates


def build_models():
    path = os.path.join(REPO, "nodes", "models_config.py")
    spec = importlib.util.spec_from_file_location("models_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    maps = {}
    for name in dir(module):
        value = getattr(module, name)
        if name.endswith("_MODEL_MAP") and isinstance(value, dict):
            maps[name] = value
    asr = getattr(module, "SEED_ASR_MODELS", {})  # UI name -> (mode, resource ID)
    if asr:
        maps["SEED_ASR_MODELS"] = {name: value[1] for name, value in asr.items()}
    return {
        "maps": maps,
        "retired": {k: {"model_id": v[0], "replacement": v[1]} for k, v in getattr(module, "RETIRED_MODELS", {}).items()},
        "region_exclusions": {k: list(v) for k, v in getattr(module, "MODEL_REGION_EXCLUSIONS", {}).items()},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--object-info", required=True, help="URL or file of ComfyUI's /object_info")
    args = parser.parse_args()
    if args.object_info.startswith("http"):
        object_info = json.load(urllib.request.urlopen(args.object_info))
    else:
        object_info = json.load(open(args.object_info))
    nodes = build_nodes(object_info)
    templates = build_templates(nodes, object_info)
    models = build_models()
    data_dir = os.path.join(SITE, "src", "data")
    os.makedirs(data_dir, exist_ok=True)
    for name, value in (("nodes", nodes), ("templates", templates), ("models", models)):
        with open(os.path.join(data_dir, name + ".json"), "w", encoding="utf-8") as file:
            json.dump(value, file, indent=1, ensure_ascii=False)
            file.write("\n")
    print(f"{len(nodes)} nodes, {len(templates)} templates, {len(models['maps'])} model maps")


if __name__ == "__main__":
    main()
