"""Regressions for stable media sockets and ComfyUI's actual V3 argument binding."""

import importlib
import os
import sys
import types
import unittest

COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
PACKAGE_NAME = "byteplus_plugin_test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
requires_comfyui = unittest.skipUnless(COMFY_ROOT, "Set COMFYUI_ROOT to run node tests.")

if COMFY_ROOT:
    sys.path.insert(0, COMFY_ROOT)
    importlib.import_module("utils")
    import comfy.cli_args

    comfy.cli_args.args.cpu = True
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [ROOT]
        sys.modules[PACKAGE_NAME] = package


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


@requires_comfyui
class MediaInputTests(unittest.TestCase):
    def setUp(self):
        self.helper = importlib.import_module(f"{PACKAGE_NAME}.nodes.media_inputs")
        self.io = importlib.import_module("comfy_api.latest._io")
        self.torch = importlib.import_module("torch")

    def test_tensor_values_are_never_tested_for_truth_or_equality(self):
        image = self.torch.zeros((1, 300, 300, 3))
        options = {"model": "example", "image": None}
        result = self.helper.merge_media_inputs(options, image=image, images={"image_1": image})
        self.assertIs(result["image"], image)
        self.assertIs(result["images"]["image_1"], image)
        self.assertIsNone(options["image"])

    def test_conflicting_sources_fail_instead_of_dropping_media(self):
        for old, new in ((object(), object()), ({"image_1": object()}, {"image_2": object()})):
            with self.assertRaisesRegex(self.helper.BytePlusException, "both at the top level"):
                self.helper.merge_media_inputs({"image": old}, image=new)

    def test_empty_groups_do_not_hide_existing_media(self):
        image = object()
        for empty in (None, {}, {"image_1": None}):
            result = self.helper.merge_media_inputs({"images": {"image_1": image}}, images=empty)
            self.assertIs(result["images"]["image_1"], image)

    def test_all_media_is_top_level_once_and_native_v3_routes_it(self):
        cases = (
            (
                "nodes_seed_audio15",
                "BytePlusSeedAudio15",
                "mode",
                {"audio", "video", "reference_image", *(f"reference_audio_{n}" for n in range(1, 7))},
            ),
            (
                "nodes_speech",
                "BytePlusSeedAudio",
                "reference_mode",
                {"reference_image", *(f"reference_audio_{n}" for n in range(1, 4))},
            ),
            ("nodes_seed", "BytePlusSeed", "model", {"images", "videos", "audios"}),
            ("nodes_seedream", "BytePlusSeedream", "model", {"images", "reference_mask"}),
            ("nodes_seedream", "BytePlusSeedreamLayerSeparation", "model", {"image"}),
            (
                "nodes_seedance2",
                "BytePlusSeedance2Reference",
                "model",
                {"reference_images", "reference_videos", "reference_audios"},
            ),
        )
        for module, name, combo_id, media in cases:
            node = getattr(importlib.import_module(f"{PACKAGE_NAME}.nodes.{module}"), name)
            schema = node.define_schema()
            top = {item.id: item for item in schema.inputs}
            self.assertEqual(len(top), len(schema.inputs))
            self.assertTrue(media <= top.keys())
            for option in top[combo_id].options:
                with self.subTest(node=name, option=option.key):
                    self.assertFalse(media & {item.id for item in option.inputs})
                    live = {combo_id: option.key}
                    for key in media:
                        item = top[key]
                        wire = f"{key}.{item.template.names[0]}" if isinstance(item, self.io.Autogrow.Input) else key
                        live[wire] = object()
                    finalized, _, v3_data = self.io.get_finalized_class_inputs(node.INPUT_TYPES(), live)
                    bound = self.io.build_nested_inputs(live, v3_data)
                    self.assertEqual(bound[combo_id][combo_id], option.key)
                    self.assertFalse(media & bound[combo_id].keys())
                    self.assertTrue(media <= bound.keys())
                    self.assertFalse(
                        any(
                            key.startswith(combo_id + ".") and key.split(".")[1] in media
                            for key in finalized["required"]
                        )
                    )
