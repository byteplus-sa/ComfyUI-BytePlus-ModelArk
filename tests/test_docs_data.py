"""
The docs site (docs-site/) lists every node and template. These checks need no ComfyUI:
they fail when a node or template is added or removed without regenerating the docs data
(see docs-site/CONTRIBUTING-DOCS.md).

  python -m unittest tests.test_docs_data
"""

import importlib.util
import json
import os
import re
import struct
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs-site", "src", "data")
PAGES = os.path.join(ROOT, "docs-site", "src", "content", "docs")
DEV_ONLY = {"BytePlusProgressTest"}


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as file:
        return json.load(file)


def generate_data_module():
    path = os.path.join(ROOT, "docs-site", "scripts", "generate_data.py")
    spec = importlib.util.spec_from_file_location("generate_data", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def node_classes():
    found = set()
    folder = os.path.join(ROOT, "nodes")
    for name in os.listdir(folder):
        if name.endswith(".py"):
            with open(os.path.join(folder, name), encoding="utf-8") as file:
                found.update(re.findall(r"^class (BytePlus\w+)\([^)]*comfy_io\.ComfyNode\)", file.read(), re.M))
    return found - DEV_ONLY


class DocsDataTests(unittest.TestCase):
    def test_every_node_class_is_in_the_docs_data(self):
        documented = {node["id"] for node in load("nodes.json")}
        self.assertEqual(node_classes() - documented, set(), "run docs-site/scripts/generate_data.py")
        self.assertEqual(documented - node_classes(), set(), "the docs list a node that no longer exists")

    def test_every_template_is_in_the_docs_data(self):
        files = {f for f in os.listdir(os.path.join(ROOT, "example_workflows")) if f.endswith(".json")}
        documented = {t["file"] for t in load("templates.json")}
        self.assertEqual(files, documented, "run docs-site/scripts/generate_data.py")

    def test_every_template_group_is_visible_in_the_template_index(self):
        with open(os.path.join(PAGES, "templates", "index.mdx"), encoding="utf-8") as file:
            visible = set(re.findall(r'<TemplateGrid\s+group="([^"]+)"', file.read()))
        self.assertEqual(visible, {template["group"] for template in load("templates.json")})

    def test_downloadable_workflows_match_the_shipped_templates(self):
        for template in load("templates.json"):
            with self.subTest(template=template["file"]):
                source = os.path.join(ROOT, "example_workflows", template["file"])
                download = os.path.join(ROOT, "docs-site", "public", "workflows", template["slug"] + ".json")
                with open(source, "rb") as source_file, open(download, "rb") as download_file:
                    self.assertEqual(source_file.read(), download_file.read())

    def test_the_model_table_matches_models_config(self):
        self.assertEqual(
            generate_data_module().build_models(), load("models.json"), "run docs-site/scripts/generate_data.py"
        )

    def test_every_node_and_template_has_a_page(self):
        for node in load("nodes.json"):
            self.assertTrue(os.path.exists(os.path.join(PAGES, "nodes", node["slug"] + ".mdx")), node["id"])
        for template in load("templates.json"):
            self.assertTrue(
                os.path.exists(os.path.join(PAGES, "templates", template["slug"] + ".mdx")), template["file"]
            )

    def test_seed_audio_versions_keep_distinct_keys_and_first_run_guidance(self):
        nodes = {node["id"]: node for node in load("nodes.json")}
        self.assertEqual(nodes["BytePlusSeedAudio15"]["keys"], ["ModelArk API key"])
        self.assertEqual(nodes["BytePlusSeedAudio"]["keys"], ["Seed Speech API key"])
        with open(os.path.join(PAGES, "nodes", "seed-audio-1-5.mdx"), encoding="utf-8") as file:
            guide = file.read()
        for required in (
            "0.13.0",
            "dola-seed-audio-1-5-261128",
            "ModelArk API key",
            "Generate your first clip",
            "ap-southeast-1",
        ):
            with self.subTest(contract=required):
                self.assertIn(required, guide)
        mode = next(item for item in nodes["BytePlusSeedAudio15"]["inputs"] if item["name"] == "mode")
        for option in mode["options"]:
            with self.subTest(mode=option):
                self.assertIn(f"**{option}:**", guide.lower())

    def test_seed_audio15_is_linked_from_shared_guides_and_existing_audio_pages(self):
        for page in (
            "concepts/keys-and-regions.mdx",
            "concepts/billing-and-cost.mdx",
            "concepts/reference-media.mdx",
            "concepts/interrupts.mdx",
            "troubleshooting.mdx",
            "nodes/seed-audio.mdx",
            "templates/seed-audio.mdx",
        ):
            with self.subTest(page=page):
                with open(os.path.join(PAGES, page), encoding="utf-8") as file:
                    self.assertIn("seed-audio-1-5/", file.read())

    def test_media_input_pages_include_current_node_views(self):
        views = {
            "byteplus-llm": ("byteplus-llm",),
            "seed-audio": ("seed-audio",),
            "seed-audio-1-5": ("seed-audio-1-5", "seed-audio-1-5-video-reference", "seed-audio-1-5-nodes-2"),
            "seedream": ("seedream", "seedream-5-5"),
            "seedream-layer-separation": ("seedream-layer-separation", "seedream-layer-separation-5-5"),
            "seedance-2-5-reference": ("seedance-2-5-reference",),
        }
        for page, screenshots in views.items():
            with open(os.path.join(PAGES, "nodes", page + ".mdx"), encoding="utf-8") as file:
                content = file.read()
            self.assertIn("left", content, page)
            for name in screenshots:
                with self.subTest(page=page, screenshot=name):
                    self.assertIn(f'<Shot src="nodes/{name}.png"', content)
                    path = os.path.join(ROOT, "docs-site", "src", "assets", "nodes", name + ".png")
                    with open(path, "rb") as image:
                        header = image.read(24)
                    self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
                    self.assertEqual(header[12:16], b"IHDR")
                    width, height = struct.unpack(">II", header[16:24])
                    self.assertGreaterEqual(width, 400)
                    self.assertGreaterEqual(height, 300)

    def test_custom_apps_have_navigation_and_local_screenshots(self):
        site = os.path.join(ROOT, "docs-site")
        with open(os.path.join(site, "astro.config.mjs"), encoding="utf-8") as file:
            sidebar = file.read()
        self.assertIn("label: 'Custom Apps'", sidebar)
        for slug in ("custom-apps", "custom-apps/avatar-studio"):
            self.assertIn(f"slug: '{slug}'", sidebar)

        for filename in ("index.mdx", "avatar-studio.mdx"):
            with self.subTest(page=filename):
                with open(os.path.join(PAGES, "custom-apps", filename), encoding="utf-8") as file:
                    page = file.read()
                self.assertRegex(page, r'^---\ntitle: "[^"\n]+"\ndescription: "[^"\n]{40,160}"\n---')

        with open(os.path.join(PAGES, "custom-apps", "avatar-studio.mdx"), encoding="utf-8") as file:
            guide = file.read()
        shots = re.findall(r'<Shot\s+src="([^"]+)"', guide)
        self.assertEqual(
            {os.path.basename(shot) for shot in shots},
            {
                "avatar-studio.png",
                "desktop-menu.png",
                "desktop-context-menu.png",
                "byteplus-api-keys.png",
                "studio-settings-menu.png",
                "studio-api-keys.png",
                "studio-upload-key.png",
            },
        )
        for shot in shots:
            with self.subTest(image=shot):
                with open(os.path.join(site, "src", "assets", shot), "rb") as file:
                    header = file.read(24)
                self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
                self.assertGreaterEqual(int.from_bytes(header[16:20], "big"), 400)

    def test_avatar_studio_guide_preserves_installation_and_key_boundaries(self):
        with open(os.path.join(PAGES, "custom-apps", "avatar-studio.mdx"), encoding="utf-8") as file:
            guide = file.read()
        for required in (
            "0.31.0",
            "ComfyUI-BytePlus-ModelArk 0.10.0",
            "separate extension",
            "ComfyUI/custom_nodes/",
            "studio_dist/",
            "default external browser",
            "ModelArk API key",
            "Seed Speech API key",
            "OmniHuman Vision AI API key",
            "ComfyUI login",
            "Comfy.org upload key",
            "plaintext file, not encrypted storage",
            "approve both",
            "never retried automatically",
        ):
            with self.subTest(contract=required):
                self.assertIn(required, guide)
        self.assertNotIn("raw.githubusercontent.com", guide)
        self.assertNotRegex(guide, r"\]\(/(?!/)")

    def test_a_removed_template_leaves_nothing_behind(self):
        slugs = {t["slug"] for t in load("templates.json")}
        site = os.path.join(ROOT, "docs-site")
        pages = {
            f[:-4] for f in os.listdir(os.path.join(PAGES, "templates")) if f.endswith(".mdx") and f != "index.mdx"
        }
        downloads = {f[:-5] for f in os.listdir(os.path.join(site, "public", "workflows"))}
        images = {
            f.rsplit("-workflow.", 1)[0].rsplit("-result.", 1)[0].rsplit(".jpg", 1)[0]
            for f in os.listdir(os.path.join(site, "src", "assets", "templates"))
        }
        self.assertEqual(pages, slugs)
        self.assertEqual(downloads, slugs)
        self.assertEqual(images, slugs)
        course_media = set()
        course = os.path.join(PAGES, "tutorials")
        if os.path.isdir(course):
            for filename in os.listdir(course):
                if filename.endswith(".mdx"):
                    with open(os.path.join(course, filename), encoding="utf-8") as file:
                        course_media.update(re.findall(r'<Media\b[^>]*\bfile="([^"]+)"', file.read()))
        for name in os.listdir(os.path.join(site, "public", "media")):
            stem = name.rsplit(".", 1)[0]
            self.assertTrue(
                stem in slugs or stem.rsplit("-", 1)[0] in slugs or name in course_media,
                f"orphan sample media: {name}",
            )
        self.assertEqual(set(load("evidence.json")), slugs)

    def test_the_site_is_left_out_of_the_registry_package(self):
        with open(os.path.join(ROOT, ".comfyignore"), encoding="utf-8") as file:
            self.assertIn("docs-site/", file.read())


if __name__ == "__main__":
    unittest.main()
