import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs-site"
COURSE = DOCS / "src/content/docs/tutorials"
LESSONS = (
    "install-comfyui",
    "set-up-api-key",
    "build-your-first-workflow",
    "refine-product-images",
    "image-to-video",
    "final-project",
)


class CourseMaterialsTests(unittest.TestCase):
    def test_all_six_lessons_are_linked_from_the_overview(self):
        overview = (COURSE / "index.mdx").read_text()
        for lesson in LESSONS:
            with self.subTest(lesson=lesson):
                self.assertTrue((COURSE / f"{lesson}.mdx").is_file())
                self.assertIn(f"./{lesson}/", overview)

    def test_each_lesson_has_a_navigation_image(self):
        for lesson in LESSONS:
            with self.subTest(lesson=lesson):
                self.assertRegex((COURSE / f"{lesson}.mdx").read_text(), r"<Shot\s")

    def test_all_course_screenshots_exist_and_have_descriptive_alt_text(self):
        for page in COURSE.glob("*.mdx"):
            for attributes in re.findall(r"<Shot\s+([^>]+)", page.read_text()):
                with self.subTest(page=page.name, image=attributes):
                    source = re.search(r'src="([^"]+)"', attributes)
                    alternative = re.search(r'alt="([^"]+)"', attributes)
                    self.assertIsNotNone(source)
                    self.assertIsNotNone(alternative)
                    self.assertTrue((DOCS / "src/assets" / source.group(1)).is_file())
                    self.assertGreater(len(alternative.group(1)), 20)

    def test_lessons_have_sequential_next_and_previous_links(self):
        for index, lesson in enumerate(LESSONS):
            text = (COURSE / f"{lesson}.mdx").read_text()
            with self.subTest(lesson=lesson):
                if index:
                    self.assertIn(f"../{LESSONS[index - 1]}/", text)
                if index < len(LESSONS) - 1:
                    self.assertIn(f"../{LESSONS[index + 1]}/", text)

    def test_practice_logo_has_a_download_and_license(self):
        self.assertTrue((DOCS / "public/course/comfyui_logo.png").is_file())
        license_text = (DOCS / "public/course/LOGO-LICENSE.txt").read_text()
        self.assertIn("MIT License", license_text)
        for lesson in ("set-up-api-key", "build-your-first-workflow"):
            with self.subTest(lesson=lesson):
                self.assertIn(
                    "/course/comfyui_logo.png",
                    (COURSE / f"{lesson}.mdx").read_text(),
                )

    def test_course_does_not_require_a_preinstalled_logo(self):
        for page in COURSE.glob("*.mdx"):
            with self.subTest(page=page.name):
                self.assertNotIn("bundled `comfyui_logo.png`", page.read_text())

    def test_generation_lessons_start_with_the_included_templates(self):
        for lesson, heading, template in (
            ("set-up-api-key", "Generate your first image", "Seedream"),
            ("image-to-video", "Prepare your shot", "Image to Draft to 1080p"),
        ):
            text = (COURSE / f"{lesson}.mdx").read_text()
            section = text.split(f"## {heading}", 1)[1].split("\n## ", 1)[0]
            first_step = re.search(r"^1\. (.+)$", section, re.MULTILINE)
            with self.subTest(lesson=lesson):
                self.assertIsNotNone(first_step)
                self.assertIn("**Templates**", first_step.group(1))
                self.assertIn("**ComfyUI-BytePlus-ModelArk**", first_step.group(1))
                self.assertIn(f"**{template}**", first_step.group(1))
                self.assertNotIn("Download workflow", first_step.group(1))

    def test_downloads_are_optional_and_have_json_import_instructions(self):
        for lesson in (
            "install-comfyui",
            "set-up-api-key",
            "build-your-first-workflow",
            "image-to-video",
        ):
            text = (COURSE / f"{lesson}.mdx").read_text()
            with self.subTest(lesson=lesson):
                self.assertIn("optional", text.lower())
                self.assertIn("save the JSON", text)
                self.assertIn("drag", text)
                self.assertIn("canvas", text)
        overview = (COURSE / "index.mdx").read_text()
        self.assertIn("included with the node pack", overview)
        self.assertIn("reference image", overview)
        self.assertIn("Module 3", overview)

    def test_module_three_teaches_importing_the_learners_own_export(self):
        text = (COURSE / "build-your-first-workflow.mdx").read_text()
        section = text.split("## Save and reopen", 1)[1].split("\n## ", 1)[0]
        self.assertIn("**Graph**", section)
        self.assertIn("**Export**", section)
        self.assertIn("first-seedream-workflow.json", section)
        self.assertIn("drag the saved JSON onto the canvas", section)
        self.assertIn("reference image", section)


if __name__ == "__main__":
    unittest.main()
