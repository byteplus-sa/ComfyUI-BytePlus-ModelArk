import json
import os
import unittest


PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_DIR = os.path.join(PLUGIN_ROOT, "example_workflows")
EXPECTED_WORKFLOWS = {
    "2.5 Model Updates.json",
    "QuotaSettings.json",
    "Seedance 1.json",
    "Seedance 2.json",
    "Seed Audio.json",
    "Seed Speech TTS and ASR.json",
    "Seed Voice Clone.json",
    "Seedream.json",
    "Seedream Layer Separation.json",
    "Seed.json",
    "vCube Video Enhance.json",
    "Video Smoothness Enhance.json",
    "Image Quality Enhance.json",
}


def load_workflow(name):
    with open(os.path.join(WORKFLOW_DIR, name), "r", encoding="utf-8") as file:
        return json.load(file)


class WorkflowTemplateTests(unittest.TestCase):

    SEEDANCE1_BEFORE_FRAMES = ["client", "model", "prompt"]
    SEEDANCE1_AFTER_FRAMES = [
        "resolution", "aspect_ratio", "duration", "seed", "camera_fixed", "watermark",
        "enable_offline_inference", "generation_count", "non_blocking",
    ]
    # BytePlusSeedream / BytePlusSeedreamLayerSeparation (core-style): inputs per
    # selected model; widgets_values index of the model value.
    SEEDREAM_INPUTS_HEAD = [
        "client", "prompt", "model", "model.size_preset", "model.width", "model.height",
    ]
    SEEDREAM_BATCH_INPUTS = SEEDREAM_INPUTS_HEAD + [
        "model.max_images", "model.images.image_1", "model.fail_on_partial", "model.seed",
        "model.watermark", "model.thinking", "model.generation_count",
    ]
    SEEDREAM_INPUT_ORDERS = {
        "seedream 5.0 pro": SEEDREAM_INPUTS_HEAD + [
            "model.images.image_1", "model.prompt_optimization", "model.seed",
            "model.watermark", "model.thinking", "model.generation_count",
            "model.output_format", "model.background", "model.reference_mask",
        ],
        "seedream 5.0 flash": SEEDREAM_INPUTS_HEAD + [
            "model.images.image_1", "model.seed", "model.watermark",
            "model.generation_count", "model.output_format", "model.background",
            "model.reference_mask",
        ],
        "seedream 5.0 lite": SEEDREAM_BATCH_INPUTS,
        "seedream-4-5-251128": SEEDREAM_BATCH_INPUTS,
        "seedream-4-0-250828": SEEDREAM_BATCH_INPUTS,
    }
    LAYER_SEPARATION_INPUT_ORDERS = {
        "seedream 5.0 pro": [
            "client", "model", "model.image", "model.prompt", "model.size", "model.seed",
            "model.prompt_optimization", "model.watermark", "model.crop_layers",
            "model.output_format", "model.save_layers", "model.filename_prefix",
        ],
        "seedream 5.0 flash": [
            "client", "model", "model.image", "model.prompt", "model.size", "model.seed",
            "model.watermark", "model.crop_layers", "model.output_format",
            "model.save_layers", "model.filename_prefix",
        ],
    }
    MODEL_KEYED_INPUT_ORDERS = {
        # node type: (widgets_values index of the model, orders per model)
        "BytePlusSeedream": (1, SEEDREAM_INPUT_ORDERS),
        "BytePlusSeedreamLayerSeparation": (0, LAYER_SEPARATION_INPUT_ORDERS),
    }
    # Core-style Seedance 2 / 2.5 nodes: client first, core's inputs, then our
    # extras; the DynamicCombo children depend on the model option (widgets_values[0]).
    CORE_STYLE_EXTRAS = ["generation_count", "non_blocking"]
    CORE_STYLE_SEEDANCE2_NODES = {
        "BytePlusSeedance2TextToVideo",
        "BytePlusSeedance2FirstLastFrame",
        "BytePlusSeedance2Reference",
    }
    AUTOGROW_SOCKET_PATTERN = (
        r"^model\.reference_(images\.image|videos\.video|audios\.audio|assets\.asset)_\d+$"
    )

    @classmethod
    def core_style_seedance2_inputs(cls, node_type, label):
        is_25 = label.startswith("Seedance 2.5")
        text = ["model.prompt", "model.resolution", "model.ratio", "model.duration", "model.generate_audio"]
        if node_type == "BytePlusSeedance2FirstLastFrame":
            if is_25:
                text = [name for name in text if name != "model.ratio"] + ["model.output_format"]
            return (
                ["client", "model", *text, "seed", "watermark", "first_frame", "last_frame",
                 "first_frame_asset_id", "last_frame_asset_id"]
                + cls.CORE_STYLE_EXTRAS
            )
        if node_type == "BytePlusSeedance2Reference":
            if is_25:
                text = text + ["model.task_type", "model.output_format"]
            # Autogrow sockets (model.reference_*) are checked separately.
            text = text + ["model.auto_downscale", "model.auto_upscale"]
        elif is_25:
            text = text + ["model.output_format"]
        return ["client", "model", *text, "seed", "watermark"] + cls.CORE_STYLE_EXTRAS

    CURRENT_INPUT_ORDERS = {
        "BytePlusAPIClient": ["new_api_key", "new_key_name", "key_name", "region"],
        "BytePlusQuotaSettings": [
            "client", "image_model", "image_limit", "video_model", "video_limit"
        ],
        # Core-style Seedance 1.x: client, core's inputs, then this pack's extras.
        "BytePlusSeedanceTextToVideo": SEEDANCE1_BEFORE_FRAMES + SEEDANCE1_AFTER_FRAMES,
        "BytePlusSeedanceImageToVideo": SEEDANCE1_BEFORE_FRAMES + ["image"]
        + SEEDANCE1_AFTER_FRAMES,
        "BytePlusSeedanceFirstLastFrame": SEEDANCE1_BEFORE_FRAMES
        + ["first_frame", "last_frame"] + SEEDANCE1_AFTER_FRAMES,
        "BytePlusSpeechClient": ["new_api_key", "new_key_name", "key_name", "region"],
        "BytePlusMediaKitClient": ["new_api_key", "new_key_name", "key_name", "region"],
        # As the frontend saves it: sockets, then the DynamicCombo children before their parent.
        "BytePlusVideoEnhance": [
            "mediakit_client", "video", "tool_version.scene", "tool_version.enhance_style",
            "tool_version", "resolution", "fps", "bitrate_level", "video_url", "bitrate",
            "comparison", "compare_time",
        ],
        "BytePlusVideoSmoothness": [
            "mediakit_client", "video", "periodic_stutter.align_source_fps",
            "periodic_stutter.insert_frame_indices", "periodic_stutter", "duplicate_frames",
            "video_url", "comparison",
        ],
        "BytePlusImageEnhance": [
            "mediakit_client", "image", "tool_version", "output_size.multiple", "output_size", "image_url",
        ],
        "BytePlusSeedAudio": [
            "speech_client", "text_prompt", "reference_mode", "reference_mode.preset_voice",
            "sample_rate", "speech_rate", "loudness_rate", "pitch_rate", "seed", "model",
            "audio_format", "enable_subtitle", "aigc_watermark", "aigc_metadata",
            "content_producer", "produce_id", "content_propagator", "propagate_id",
        ],
        "BytePlusSeed": [
            "client", "prompt", "model", "model.images.image_1", "model.videos.video_1",
            "model.temperature", "seed", "system_prompt", "detail", "fps", "reasoning_mode",
            "reasoning_effort", "turns", "stream", "file_expire_seconds",
        ],
        "BytePlusSeedTTS": [
            "speech_client", "model", "text", "voice", "custom_speaker_id", "context_text",
            "emotion", "emotion_scale", "speech_rate", "loudness_rate", "pitch",
            "sample_rate", "explicit_language", "silence_duration", "filter_markdown",
            "enable_subtitle", "detect_language", "context_language", "read_emoji",
            "read_latex", "read_parentheses", "unsupported_char_ratio", "use_cache",
            "tone_fidelity", "seed",
        ],
        "BytePlusSeedASR": [
            "speech_client", "model", "audio_url", "language", "enable_punc", "enable_itn",
            "enable_ddc", "enable_speaker_info", "hotwords", "context_text",
            "context_image_url", "enable_auto_lang", "enable_lid", "enable_channel_split",
            "vad_segment", "end_window_size", "output_zh_variant",
            "filter_system_sensitive_words", "remove_words", "mask_words",
            "wrap_sensitive_words", "audio_format", "audio", "context_image",
        ],
        "BytePlusSeedVoiceClone": [
            "speech_client", "speaker_id", "language", "reference_text", "demo_text",
            "disable_volume_normalization", "audio",
        ],
        "BytePlusSeedanceDraftToFinal": [
            "client", "draft_task_id", "watermark", "generation_count", "non_blocking",
        ],
    }

    def test_template_set_is_current_and_has_no_third_party_nodes(self):
        actual = {
            name
            for name in os.listdir(WORKFLOW_DIR)
            if name.lower().endswith(".json")
        }
        self.assertEqual(actual, EXPECTED_WORKFLOWS)

        forbidden_types = {"Fast Groups Bypasser (rgthree)", "ShowText|pysssss", "Note"}
        for name in sorted(actual):
            with self.subTest(workflow=name):
                workflow = load_workflow(name)
                node_types = {node["type"] for node in workflow["nodes"]}
                self.assertTrue(node_types.isdisjoint(forbidden_types))
                self.assertEqual(workflow["version"], 0.4)

    def test_links_and_node_versions_are_consistent(self):
        for name in sorted(EXPECTED_WORKFLOWS):
            with self.subTest(workflow=name):
                workflow = load_workflow(name)
                nodes = {node["id"]: node for node in workflow["nodes"]}
                self.assertEqual(len(nodes), len(workflow["nodes"]))
                self.assertGreaterEqual(workflow["last_node_id"], max(nodes))

                link_ids = set()
                for link in workflow["links"]:
                    link_id, origin_id, origin_slot, target_id, target_slot, link_type = link
                    self.assertNotIn(link_id, link_ids)
                    link_ids.add(link_id)
                    self.assertIn(origin_id, nodes)
                    self.assertIn(target_id, nodes)
                    origin = nodes[origin_id]
                    target = nodes[target_id]
                    self.assertLess(origin_slot, len(origin.get("outputs", [])))
                    self.assertLess(target_slot, len(target.get("inputs", [])))
                    self.assertEqual(target["inputs"][target_slot]["link"], link_id)
                    target_type = target["inputs"][target_slot]["type"].split(",")[0]
                    if target_type != "*":  # wildcard inputs, e.g. PreviewAny
                        self.assertEqual(target_type, link_type)
                    self.assertIn(link_id, origin["outputs"][origin_slot]["links"])

                self.assertGreaterEqual(
                    workflow["last_link_id"], max(link_ids, default=0)
                )
                for node in workflow["nodes"]:
                    if node["type"].startswith("BytePlus"):
                        self.assertEqual(node["properties"]["cnr_id"], "ComfyUI-BytePlus-ModelArk")
                        self.assertEqual(node["properties"]["ver"], "0.3.0")

    def test_dynamic_combo_templates_use_v3_namespaced_inputs(self):
        combo_inputs = {
            "BytePlusSeedance2": "model_version",
            "BytePlusSeedream5": "model_version",
            **{node_type: "model" for node_type in self.CORE_STYLE_SEEDANCE2_NODES},
        }
        for name in ("Seedance 2.json", "2.5 Model Updates.json"):
            workflow = load_workflow(name)
            for node in workflow["nodes"]:
                combo = combo_inputs.get(node["type"])
                if combo is None:
                    continue
                inputs = node["inputs"]
                model_input = next(item for item in inputs if item["name"] == combo)
                self.assertEqual(model_input["type"], "COMFY_DYNAMICCOMBO_V3")
                self.assertTrue(
                    any(item["name"].startswith(combo + ".") for item in inputs)
                )
                self.assertFalse(any(item["name"] == "prompt" for item in inputs))

    def test_plugin_input_order_matches_current_schema(self):
        found_types = set()
        for name in sorted(EXPECTED_WORKFLOWS):
            workflow = load_workflow(name)
            for node in workflow["nodes"]:
                names = [item["name"] for item in node["inputs"]]
                if node["type"] in self.MODEL_KEYED_INPUT_ORDERS:
                    index, orders = self.MODEL_KEYED_INPUT_ORDERS[node["type"]]
                    expected = orders[node["widgets_values"][index]]
                elif node["type"] in self.CORE_STYLE_SEEDANCE2_NODES:
                    expected = self.core_style_seedance2_inputs(
                        node["type"], node["widgets_values"][0]
                    )
                    sockets = [n for n in names if n.startswith("model.reference_")]
                    for socket in sockets:
                        self.assertRegex(socket, self.AUTOGROW_SOCKET_PATTERN)
                    names = [n for n in names if n not in sockets]
                else:
                    expected = self.CURRENT_INPUT_ORDERS.get(node["type"])
                if expected is None:
                    continue
                found_types.add(node["type"])
                self.assertEqual(names, expected, msg=f"{name}: {node['type']}")
        self.assertEqual(
            found_types,
            set(self.CURRENT_INPUT_ORDERS)
            | set(self.MODEL_KEYED_INPUT_ORDERS)
            | self.CORE_STYLE_SEEDANCE2_NODES,
        )

    def test_core_style_seedream_templates(self):
        seedream = next(
            node for node in load_workflow("Seedream.json")["nodes"]
            if node["type"] == "BytePlusSeedream"
        )
        layers = next(
            node for node in load_workflow("Seedream Layer Separation.json")["nodes"]
            if node["type"] == "BytePlusSeedreamLayerSeparation"
        )
        for node in (seedream, layers):
            inputs = {item["name"]: item for item in node["inputs"]}
            self.assertEqual(inputs["model"]["type"], "COMFY_DYNAMICCOMBO_V3")
            # Option inputs are namespaced under the DynamicCombo, never bare.
            self.assertNotIn("seed", inputs)
            self.assertNotIn("watermark", inputs)
            self.assertEqual(inputs["model.seed"]["type"], "INT")

        values = seedream["widgets_values"]
        self.assertTrue(values[0].strip())  # core rejects an empty prompt
        self.assertEqual(values[1:5], ["seedream 5.0 pro", "(2K) 2048x2048 (1:1)", 2048, 2048])
        self.assertEqual(values[5], "standard")  # prompt_optimization
        self.assertEqual(values[6:8], [42, "randomize"])  # seed + control value
        self.assertEqual(values[8:], [False, True, 1, "jpeg", "opaque"])
        self.assertEqual(
            [output["name"] for output in seedream["outputs"]], ["IMAGE", "response", "mask"]
        )

        self.assertEqual(
            layers["widgets_values"],
            ["seedream 5.0 pro", "", "auto", 42, "randomize", "standard", False, False,
             "png", False, "BytePlus/Layers/Seedream"],
        )
        self.assertEqual(
            [output["name"] for output in layers["outputs"]],
            ["base_image", "base_mask", "layers", "masks", "bboxes", "layer_stack", "layers_json"],
        )

    def test_seedance2_template_uses_core_style_nodes(self):
        workflow = load_workflow("Seedance 2.json")
        nodes = {node["id"]: node for node in workflow["nodes"]}
        types = {node["type"] for node in nodes.values()}
        self.assertNotIn("BytePlusSeedance2", types)  # legacy node
        self.assertTrue(
            {"BytePlusAPIClient", "BytePlusSeedanceDraftToFinal", "SaveVideo",
             "LoadImage", "LoadVideo", "LoadAudio"}
            | self.CORE_STYLE_SEEDANCE2_NODES
            <= types
        )
        labels = {
            node["widgets_values"][0]
            for node in nodes.values()
            if node["type"] in self.CORE_STYLE_SEEDANCE2_NODES
        }
        self.assertTrue(any(label.startswith("Seedance 2.0") for label in labels))
        self.assertTrue(any(label.startswith("Seedance 2.5") for label in labels))
        # Draft to Final is fed from the draft_task_id output (slot 1) of a Draft run.
        final = next(n for n in nodes.values() if n["type"] == "BytePlusSeedanceDraftToFinal")
        draft_input = next(i for i in final["inputs"] if i["name"] == "draft_task_id")
        link = next(l for l in workflow["links"] if l[0] == draft_input["link"])
        source = nodes[link[1]]
        self.assertEqual(link[2], 1)
        self.assertEqual(source["outputs"][1]["name"], "draft_task_id")
        self.assertIn("Draft", source["widgets_values"][0])
        # widgets_values: one value per widget input, plus control_after_generate after seed.
        for node in nodes.values():
            if node["type"].startswith("BytePlus"):
                widgets = [i["name"] for i in node["inputs"] if "widget" in i]
                self.assertEqual(
                    len(node["widgets_values"]),
                    len(widgets) + widgets.count("seed"),
                    msg=node["type"],
                )
        # A seed widget is followed by its control_after_generate value.
        for node in nodes.values():
            if node["type"] in self.CORE_STYLE_SEEDANCE2_NODES:
                values = node["widgets_values"]
                control = next(
                    i for i, v in enumerate(values)
                    if v in ("fixed", "increment", "decrement", "randomize")
                )
                self.assertIsInstance(values[control - 1], int)

    def test_updates_template_uses_core_style_nodes(self):
        updates = load_workflow("2.5 Model Updates.json")
        nodes = {node["type"]: node for node in updates["nodes"]}
        for legacy in ("BytePlusSeedance2", "BytePlusSeedream5", "BytePlusVisualUnderstanding"):
            self.assertNotIn(legacy, nodes)
        # model, prompt, resolution, ratio, duration, generate_audio
        self.assertEqual(
            nodes["BytePlusSeedance2TextToVideo"]["widgets_values"][0:6:2], ["Seedance 2.5", "720p", 30]
        )
        self.assertEqual(nodes["BytePlusSeedream"]["widgets_values"][1], "seedream 5.0 pro")
        self.assertEqual(nodes["BytePlusSeed"]["widgets_values"][1], "Seed 2.1 Turbo")

    def test_seedance1_template_widget_positions(self):
        workflow = load_workflow("Seedance 1.json")
        nodes = {node["type"]: node for node in workflow["nodes"]}
        self.assertNotIn("BytePlusSeedance1", nodes)
        self.assertNotIn("BytePlusSeedance1_5", nodes)
        # Seedance 1.5 Pro is deprecated by BytePlus (shut down on 2026-11-11).
        self.assertNotIn("seedance-1-5-pro", json.dumps(workflow))
        for node_type, model in (
            ("BytePlusSeedanceTextToVideo", "seedance-1-0-pro-fast-251015"),
            ("BytePlusSeedanceImageToVideo", "seedance-1-0-pro-fast-251015"),
            ("BytePlusSeedanceFirstLastFrame", "seedance-1-0-pro-250528"),
        ):
            with self.subTest(node=node_type):
                node = nodes[node_type]
                widget_inputs = [item["name"] for item in node["inputs"] if "widget" in item]
                values = node["widgets_values"]
                # One value per widget, plus control_after_generate right after seed.
                self.assertEqual(len(values), len(widget_inputs) + 1)
                seed_index = widget_inputs.index("seed")
                self.assertEqual(values[0], model)
                self.assertTrue(values[1].strip())  # prompt (core rejects an empty one)
                self.assertEqual(values[seed_index + 1], "randomize")
                named = dict(zip(widget_inputs[: seed_index + 1], values))
                named.update(zip(widget_inputs[seed_index + 1 :], values[seed_index + 2 :]))
                self.assertEqual(named["duration"], 5)
                self.assertEqual(named["generation_count"], 1)
                for removed in ("generate_audio", "auto_duration", "draft_mode"):
                    self.assertNotIn(removed, named)
                self.assertEqual(
                    [output["name"] for output in node["outputs"]],
                    ["VIDEO", "last_frame", "response"],
                )
                # Core's optional inputs and this pack's extras are optional sockets.
                optional = {item["name"] for item in node["inputs"] if item.get("shape") == 7}
                self.assertEqual(
                    optional, set(self.SEEDANCE1_AFTER_FRAMES[3:]), msg=node_type
                )

    def test_templates_are_english_and_byteplus_only(self):
        for name in sorted(EXPECTED_WORKFLOWS):
            with open(os.path.join(WORKFLOW_DIR, name), "r", encoding="utf-8") as file:
                text = file.read()
            with self.subTest(workflow=name):
                self.assertFalse(any("\u4e00" <= char <= "\u9fff" for char in text))
                self.assertNotIn("doubao", text)
                self.assertNotIn("Jimeng", text)

    def test_vcube_template(self):
        workflow = load_workflow("vCube Video Enhance.json")
        nodes = {node["id"]: node for node in workflow["nodes"]}
        enhance = next(node for node in nodes.values() if node["type"] == "BytePlusVideoEnhance")
        self.assertEqual(enhance["widgets_values"], ["standard", "aigc", "hd", "1080p", "source", "medium", "", 0, True, -1])
        # Enhanced video and comparison to Save Video; the frame pair to ComfyUI's Compare Images.
        targets = {(link[2], nodes[link[3]]["type"]) for link in workflow["links"] if link[1] == enhance["id"]}
        self.assertEqual(targets, {(0, "SaveVideo"), (1, "SaveVideo"), (2, "ImageCompare"), (3, "ImageCompare")})

    def test_video_smoothness_template(self):
        workflow = load_workflow("Video Smoothness Enhance.json")
        nodes = {node["id"]: node for node in workflow["nodes"]}
        smooth = next(node for node in nodes.values() if node["type"] == "BytePlusVideoSmoothness")
        self.assertEqual(smooth["widgets_values"], ["repair", False, "", "remove", "", True])
        # Repaired video and side-by-side comparison to Save Video; the task JSON to Preview Any.
        targets = {(link[2], nodes[link[3]]["type"]) for link in workflow["links"] if link[1] == smooth["id"]}
        self.assertEqual(targets, {(0, "SaveVideo"), (1, "SaveVideo"), (4, "PreviewAny")})

    def test_image_quality_enhance_template(self):
        workflow = load_workflow("Image Quality Enhance.json")
        nodes = {node["id"]: node for node in workflow["nodes"]}
        enhance = next(node for node in nodes.values() if node["type"] == "BytePlusImageEnhance")
        self.assertEqual(enhance["widgets_values"], ["standard", "multiple", 2, ""])
        # The enhanced image to Save Image; original (image_a) and enhanced (image_b) to Compare Images.
        targets = {(link[2], nodes[link[3]]["type"], link[4]) for link in workflow["links"] if link[1] == enhance["id"]}
        self.assertEqual(targets, {(0, "SaveImage", 0), (1, "ImageCompare", 0), (0, "ImageCompare", 1)})

    def test_seedance_video_outputs_are_list_slots(self):
        # VIDEO is a list output (every video of a generation_count batch); the frontend
        # saves list outputs with the grid slot shape (LiteGraph GRID_SHAPE = 6).
        seedance_nodes = self.CORE_STYLE_SEEDANCE2_NODES | {
            "BytePlusSeedanceTextToVideo", "BytePlusSeedanceImageToVideo",
            "BytePlusSeedanceFirstLastFrame", "BytePlusSeedanceDraftToFinal",
        }
        found = set()
        for name in sorted(EXPECTED_WORKFLOWS):
            for node in load_workflow(name)["nodes"]:
                if node["type"] in seedance_nodes:
                    found.add(node["type"])
                    with self.subTest(workflow=name, node=node["id"]):
                        video = node["outputs"][0]
                        self.assertEqual((video["name"], video.get("shape")), ("VIDEO", 6))
                        self.assertTrue(all("shape" not in o for o in node["outputs"][1:]))
        self.assertEqual(found, seedance_nodes)

    def test_templates_do_not_embed_api_keys(self):
        for name in sorted(EXPECTED_WORKFLOWS):
            workflow = load_workflow(name)
            for node in workflow["nodes"]:
                if node["type"] in ("BytePlusAPIClient", "BytePlusSpeechClient", "BytePlusMediaKitClient"):
                    self.assertEqual(node["widgets_values"][0], "")


if __name__ == "__main__":
    unittest.main()
