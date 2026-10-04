"""
The docs site (docs-site/) lists every node and template. These checks need no ComfyUI:
they fail when a node or template is added or removed without regenerating the docs data
(see docs-site/CONTRIBUTING-DOCS.md).

  python -m unittest tests.test_docs_data
"""
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs-site", "src", "data")
PAGES = os.path.join(ROOT, "docs-site", "src", "content", "docs")
DEV_ONLY = {"BytePlusProgressTest"}


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as file:
        return json.load(file)


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

    def test_every_node_and_template_has_a_page(self):
        for node in load("nodes.json"):
            self.assertTrue(os.path.exists(os.path.join(PAGES, "nodes", node["slug"] + ".mdx")), node["id"])
        for template in load("templates.json"):
            self.assertTrue(os.path.exists(os.path.join(PAGES, "templates", template["slug"] + ".mdx")), template["file"])

    def test_a_removed_template_leaves_nothing_behind(self):
        slugs = {t["slug"] for t in load("templates.json")}
        site = os.path.join(ROOT, "docs-site")
        pages = {f[:-4] for f in os.listdir(os.path.join(PAGES, "templates")) if f.endswith(".mdx") and f != "index.mdx"}
        downloads = {f[:-5] for f in os.listdir(os.path.join(site, "public", "workflows"))}
        images = {
            f.rsplit("-workflow.", 1)[0].rsplit("-result.", 1)[0].rsplit(".jpg", 1)[0]
            for f in os.listdir(os.path.join(site, "src", "assets", "templates"))
        }
        self.assertEqual(pages, slugs)
        self.assertEqual(downloads, slugs)
        self.assertEqual(images, slugs)
        for name in os.listdir(os.path.join(site, "public", "media")):
            stem = name.rsplit(".", 1)[0]
            self.assertTrue(
                stem in slugs or stem.rsplit("-", 1)[0] in slugs, f"orphan sample media: {name}"
            )
        self.assertEqual(set(json.load(open(os.path.join(DATA, "evidence.json")))), slugs)

    def test_the_site_is_left_out_of_the_registry_package(self):
        with open(os.path.join(ROOT, ".comfyignore"), encoding="utf-8") as file:
            self.assertIn("docs-site/", file.read())


if __name__ == "__main__":
    unittest.main()
