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

    def test_image_workflow_lessons_show_the_actual_canvas_graph(self):
        for lesson, image in (
            ("build-your-first-workflow", "byteplus-image-canvas.webp"),
            ("refine-product-images", "byteplus-refine-canvas.webp"),
        ):
            text = (COURSE / f"{lesson}.mdx").read_text()
            with self.subTest(lesson=lesson):
                self.assertIn(f'src="tutorials/{image}"', text)
                self.assertTrue((DOCS / "src/assets/tutorials" / image).is_file())
                self.assertIn("Load Image", text)
                self.assertIn("Save Image", text)
        refinement = (COURSE / "refine-product-images.mdx").read_text()
        self.assertIn("## Your workflow on the canvas", refinement)
        self.assertIn("same three-node graph", refinement)

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
        self.assertTrue((DOCS / "public/course/byteplus_logo.png").is_file())
        self.assertTrue((DOCS / "public/course/byteplus-logo.svg").is_file())
        license_text = (DOCS / "public/course/LOGO-LICENSE.txt").read_text()
        self.assertIn("BytePlus", license_text)
        self.assertIn("sf-bpcms.bytepluscdn.com", license_text)
        self.assertNotIn("MIT License", license_text)
        for lesson in ("set-up-api-key", "build-your-first-workflow"):
            with self.subTest(lesson=lesson):
                self.assertIn(
                    "/course/byteplus_logo.png",
                    (COURSE / f"{lesson}.mdx").read_text(),
                )

    def test_image_lessons_use_seedream_55_and_explicit_output_settings(self):
        for lesson in (
            "set-up-api-key",
            "build-your-first-workflow",
            "refine-product-images",
            "final-project",
        ):
            text = (COURSE / f"{lesson}.mdx").read_text()
            with self.subTest(lesson=lesson):
                self.assertIn("Seedream 5.5", text)
                self.assertNotIn("5.0 Pro", text)
                self.assertNotIn("saved Pro workflow", text)
        for lesson in ("set-up-api-key", "build-your-first-workflow"):
            text = (COURSE / f"{lesson}.mdx").read_text()
            with self.subTest(lesson=lesson):
                self.assertIn("`seedream 5.5`", text)
                self.assertIn("`opaque`", text)
                self.assertIn("`png`", text)
                self.assertIn("update", text.lower())

    def test_course_does_not_present_old_logo_or_character_demo_outputs(self):
        for page in COURSE.glob("*.mdx"):
            text = page.read_text()
            with self.subTest(page=page.name):
                self.assertNotIn("/course/comfyui_logo.png", text)
                self.assertNotIn("templates/seedream-result.webp", text)
                self.assertNotIn("templates/image-edit-result.webp", text)
                self.assertNotIn("templates/image-to-draft-to-1080p-result.webp", text)
                self.assertNotIn('file="image-to-draft-to-1080p.mp4"', text)

    def test_image_seed_is_explained_as_a_local_rerun_control(self):
        text = (COURSE / "refine-product-images.mdx").read_text()
        self.assertIn("local rerun control", text)
        self.assertIn("not sent", text)
        self.assertIn("not a deterministic", text)

    def test_course_uses_dreamina_product_name_and_exact_video_ui_label(self):
        for lesson in ("index", "image-to-video", "final-project"):
            text = (COURSE / f"{lesson}.mdx").read_text()
            with self.subTest(lesson=lesson):
                self.assertIn("Dreamina Seedance 2.5", text)
        video = (COURSE / "image-to-video.mdx").read_text()
        self.assertIn("**Seedance 2.5 Draft**", video)

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

    def test_course_uses_included_templates_without_starter_downloads(self):
        for page in COURSE.glob("*.mdx"):
            text = page.read_text()
            with self.subTest(lesson=page.name):
                for attributes in re.findall(r"<TemplateFacts\s+([^>]+)", text):
                    self.assertIn("showDownload={false}", attributes)
                self.assertNotIn("optional download", text.lower())
                self.assertNotIn("fallback", text.lower())
                self.assertNotIn("/workflows/", text)
                self.assertNotRegex(text, r"click \*\*[^*]+\.json\*\*")
        overview = (COURSE / "index.mdx").read_text()
        self.assertIn("included with the node pack", overview)
        self.assertIn("Comfy Registry", overview)
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

    def test_final_project_retains_the_learners_workflow_exports(self):
        text = (COURSE / "final-project.mdx").read_text()
        self.assertIn("**Graph > Export**", text)
        self.assertIn("image-workflow.json", text)
        self.assertIn("video-workflow.json", text)
        self.assertIn("Both JSON files reopen", text)

    def test_installation_shows_how_to_reach_the_node_pack_manager(self):
        text = (COURSE / "install-comfyui.mdx").read_text()
        section = text.split("## Install the BytePlus pack", 1)[1].split("\n## ", 1)[0]
        self.assertIn("**ComfyUI menu**", section)
        self.assertIn("top left", section)
        self.assertIn("**Extensions**", section)
        self.assertIn("**Nodes Manager**", section)
        self.assertIn("**Node Pack**", section)
        self.assertLess(
            section.index('src="tutorials/extensions-entry.webp"'),
            section.index('src="tutorials/extensions-manager.webp"'),
        )


if __name__ == "__main__":
    unittest.main()
