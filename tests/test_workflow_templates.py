import importlib.util
import json
import os
import unittest


PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_DIR = os.path.join(PLUGIN_ROOT, "example_workflows")
EXPECTED_WORKFLOWS = {
    "2.5 Model Updates.json",
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
    "Text to Image to Video.json",
    "Seedance Video Extension.json",
    "Seed Prompt Writer.json",
    "Generate and Enhance.json",
    "Virtual Portrait - Existing Asset.json",
    "Virtual Portrait - New Asset.json",
}


def load_workflow(name):
    with open(os.path.join(WORKFLOW_DIR, name), "r", encoding="utf-8") as file:
        return json.load(file)


def load_models_config():
    """nodes/models_config.py has no imports, so it loads without ComfyUI."""
    path = os.path.join(PLUGIN_ROOT, "nodes", "models_config.py")
    spec = importlib.util.spec_from_file_location("byteplus_models_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkflowTemplateTests(unittest.TestCase):

    SEEDANCE1_BEFORE_FRAMES = ["model", "prompt"]
    SEEDANCE1_AFTER_FRAMES = [
        "resolution", "aspect_ratio", "duration", "seed", "camera_fixed", "watermark",
        "enable_offline_inference", "generation_count", "non_blocking",
    ]
    # BytePlusSeedream / BytePlusSeedreamLayerSeparation (core-style): inputs per
    # selected model; widgets_values index of the model value.
    SEEDREAM_INPUTS_HEAD = [
        "prompt", "model", "model.size_preset", "model.width", "model.height",
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
            "model", "model.image", "model.prompt", "model.size", "model.seed",
            "model.prompt_optimization", "model.watermark", "model.crop_layers",
            "model.output_format", "model.save_layers", "model.filename_prefix",
        ],
        "seedream 5.0 flash": [
            "model", "model.image", "model.prompt", "model.size", "model.seed",
            "model.watermark", "model.crop_layers", "model.output_format",
            "model.save_layers", "model.filename_prefix",
        ],
    }
    MODEL_KEYED_INPUT_ORDERS = {
        # node type: (widgets_values index of the model, orders per model)
        "BytePlusSeedream": (1, SEEDREAM_INPUT_ORDERS),
        "BytePlusSeedreamLayerSeparation": (0, LAYER_SEPARATION_INPUT_ORDERS),
    }
    # Seedance 2 / 2.5 nodes: core's inputs, then our extras; the DynamicCombo children depend on the model option (widgets_values[0]).
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
                ["model", *text, "seed", "watermark", "first_frame", "last_frame",
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
        return ["model", *text, "seed", "watermark"] + cls.CORE_STYLE_EXTRAS

    CURRENT_INPUT_ORDERS = {
        # Seedance 1.x: core's inputs, then this pack's extras.
        "BytePlusSeedanceTextToVideo": SEEDANCE1_BEFORE_FRAMES + SEEDANCE1_AFTER_FRAMES,
        "BytePlusSeedanceImageToVideo": SEEDANCE1_BEFORE_FRAMES + ["image"]
        + SEEDANCE1_AFTER_FRAMES,
        "BytePlusSeedanceFirstLastFrame": SEEDANCE1_BEFORE_FRAMES
        + ["first_frame", "last_frame"] + SEEDANCE1_AFTER_FRAMES,
        # As the frontend saves it: sockets, then the DynamicCombo children before their parent.
        "BytePlusVideoEnhance": [
            "video", "tool_version.scene", "tool_version.enhance_style",
            "tool_version", "resolution", "fps", "bitrate_level", "video_url", "bitrate",
            "comparison", "compare_time",
        ],
        "BytePlusVideoSmoothness": [
            "video", "periodic_stutter.align_source_fps",
            "periodic_stutter.insert_frame_indices", "periodic_stutter", "duplicate_frames",
            "video_url", "comparison",
        ],
        "BytePlusImageEnhance": [
            "image", "tool_version", "output_size.multiple", "output_size", "image_url",
        ],
        "BytePlusSeedAudio": [
            "text_prompt", "reference_mode", "reference_mode.preset_voice",
            "sample_rate", "speech_rate", "loudness_rate", "pitch_rate", "seed", "model",
            "audio_format", "enable_subtitle", "aigc_watermark", "aigc_metadata",
            "content_producer", "produce_id", "content_propagator", "propagate_id", "generation_count",
        ],
        "BytePlusSeed": [
            "prompt", "model", "model.images.image_1", "model.videos.video_1",
            "model.temperature", "seed", "system_prompt", "detail", "fps", "reasoning_mode",
            "reasoning_effort", "turns", "stream", "file_expire_seconds",
        ],
        "BytePlusSeedTTS": [
            "model", "text", "voice", "custom_speaker_id", "context_text",
            "emotion", "emotion_scale", "speech_rate", "loudness_rate", "pitch",
            "sample_rate", "explicit_language", "silence_duration", "filter_markdown",
            "enable_subtitle", "detect_language", "context_language", "read_emoji",
            "read_latex", "read_parentheses", "unsupported_char_ratio", "use_cache",
            "tone_fidelity", "seed",
        ],
        "BytePlusSeedASR": [
            "model", "audio_url", "language", "enable_punc", "enable_itn",
            "enable_ddc", "enable_speaker_info", "hotwords", "context_text",
            "context_image_url", "enable_auto_lang", "enable_lid", "enable_channel_split",
            "vad_segment", "end_window_size", "output_zh_variant",
            "filter_system_sensitive_words", "remove_words", "mask_words",
            "wrap_sensitive_words", "audio_format", "audio", "context_image",
        ],
        "BytePlusSeedVoiceClone": [
            "speaker_id", "language", "reference_text", "demo_text",
            "disable_volume_normalization", "audio",
        ],
        "BytePlusCreateImageAsset": [
            "image", "group_id", "image_url", "group_name", "asset_name", "project_name", "wait_until_active",
        ],
        "BytePlusAssetLibrary": ["group_type", "group_id", "status", "name", "max_results", "project_name"],
        "BytePlusSeedanceDraftToFinal": [
            "draft_task_id", "watermark", "generation_count", "non_blocking",
        ],
    }

    def test_template_set_is_current_and_has_no_third_party_nodes(self):
        actual = {
            name
            for name in os.listdir(WORKFLOW_DIR)
            if name.lower().endswith(".json")
        }
        self.assertEqual(actual, EXPECTED_WORKFLOWS)

        forbidden_types = {
            "Fast Groups Bypasser (rgthree)", "ShowText|pysssss", "Note",
            # Keys come from Settings > BytePlus; there are no client nodes.
            "BytePlusAPIClient", "BytePlusSpeechClient", "BytePlusMediaKitClient",
        }
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
                        self.assertEqual(node["properties"]["ver"], "0.5.0")

    def test_dynamic_combo_templates_use_v3_namespaced_inputs(self):
        combo_inputs = {
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
        self.assertTrue(
            {"BytePlusSeedanceDraftToFinal", "SaveVideo",
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
        # model, prompt, resolution, ratio, duration, generate_audio
        self.assertEqual(
            nodes["BytePlusSeedance2TextToVideo"]["widgets_values"][0:6:2], ["Seedance 2.5", "720p", 30]
        )
        self.assertEqual(nodes["BytePlusSeedream"]["widgets_values"][1], "seedream 5.0 pro")
        self.assertEqual(nodes["BytePlusSeed"]["widgets_values"][1], "Seed 2.1 Turbo")

    def test_seedance1_template_widget_positions(self):
        workflow = load_workflow("Seedance 1.json")
        nodes = {node["type"]: node for node in workflow["nodes"]}
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
                self.assertEqual(optional, set(self.SEEDANCE1_AFTER_FRAMES[3:]), msg=node_type)

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

    @staticmethod
    def edges(workflow):
        """Links as (origin type, origin slot, target type, target input name)."""
        nodes = {node["id"]: node for node in workflow["nodes"]}
        return {
            (nodes[o]["type"], os_, nodes[t]["type"], nodes[t]["inputs"][ts]["name"])
            for _id, o, os_, t, ts, _type in workflow["links"]
        }

    def test_text_to_image_to_video_template(self):
        workflow = load_workflow("Text to Image to Video.json")
        # Seedream's image is saved and is the first frame of the Seedance clip.
        self.assertEqual(self.edges(workflow), {
            ("BytePlusSeedream", 0, "SaveImage", "images"),
            ("BytePlusSeedream", 0, "BytePlusSeedance2FirstLastFrame", "first_frame"),
            ("BytePlusSeedance2FirstLastFrame", 0, "SaveVideo", "video"),
        })

    def test_video_extension_template(self):
        workflow = load_workflow("Seedance Video Extension.json")
        nodes = {node["id"]: node for node in workflow["nodes"]}
        clips = [n for n in nodes.values() if n["type"].startswith("BytePlusSeedance2")]
        self.assertEqual(len(clips), 3)
        # Each clip starts from the previous clip's last_frame (output 2)...
        chained = [
            (nodes[o]["type"], os_, nodes[t]["type"], nodes[t]["inputs"][ts]["name"], o, t)
            for _id, o, os_, t, ts, _type in workflow["links"]
            if nodes[t]["inputs"][ts]["name"] == "first_frame"
        ]
        self.assertEqual(len(chained), 2)
        self.assertTrue(all(link[1] == 2 for link in chained))
        self.assertEqual({(link[4], link[5]) for link in chained}, {(clips[0]["id"], clips[1]["id"]), (clips[1]["id"], clips[2]["id"])})
        # ...and all three videos are joined, in order, into the one Save Video.
        concat = next(n for n in nodes.values() if n["type"] == "ConcatenateVideo")
        joined = {
            nodes[t]["inputs"][ts]["name"]: o
            for _id, o, _slot, t, ts, _type in workflow["links"] if t == concat["id"]
        }
        self.assertEqual(joined, {f"videos.video{i}": clip["id"] for i, clip in enumerate(clips)})
        # Only the joined video is saved, not the three intermediate clips.
        self.assertEqual(sum(n["type"] == "SaveVideo" for n in nodes.values()), 1)

    def test_seed_prompt_writer_template(self):
        workflow = load_workflow("Seed Prompt Writer.json")
        # The LLM text is Seedream's prompt (its widget is replaced by the link) and is shown.
        self.assertEqual(self.edges(workflow), {
            ("BytePlusSeed", 0, "PreviewAny", "source"),
            ("BytePlusSeed", 0, "BytePlusSeedream", "prompt"),
            ("BytePlusSeedream", 0, "SaveImage", "images"),
        })

    def test_generate_and_enhance_template(self):
        workflow = load_workflow("Generate and Enhance.json")
        edges = self.edges(workflow)
        # ModelArk generates, MediaKit enhances.
        self.assertIn(("BytePlusSeedream", 0, "BytePlusImageEnhance", "image"), edges)
        self.assertIn(("BytePlusSeedance2TextToVideo", 0, "BytePlusVideoEnhance", "video"), edges)
        # The image branch runs by default; the (paid, slow) video branch is bypassed.
        modes = {}
        for node in workflow["nodes"]:
            modes.setdefault(node["type"], set()).add(node["mode"])
        self.assertEqual(modes["BytePlusImageEnhance"], {0})
        self.assertEqual(modes["BytePlusSeedance2TextToVideo"], {4})
        self.assertEqual(modes["BytePlusVideoEnhance"], {4})

    ASSET_SLOT = "model.reference_assets.asset_1"

    def test_virtual_portrait_existing_asset_template(self):
        workflow = load_workflow("Virtual Portrait - Existing Asset.json")
        # Option A: a pasted asset ID; option B: Asset Library looks it up by name (one result).
        self.assertEqual(self.edges(workflow), {
            ("PrimitiveString", 0, "BytePlusSeedance2Reference", self.ASSET_SLOT),
            ("BytePlusAssetLibrary", 0, "BytePlusSeedance2Reference", self.ASSET_SLOT),
            ("BytePlusAssetLibrary", 1, "PreviewAny", "source"),
            ("BytePlusSeedance2Reference", 0, "SaveVideo", "video"),
        })
        nodes = {node["type"]: node for node in workflow["nodes"]}
        library = nodes["BytePlusAssetLibrary"]
        # group_type, group_id, status, name, max_results: an asset_N slot takes one asset.
        self.assertEqual(library["widgets_values"][:5], ["AIGC", "", "Active", "My portrait", 1])
        # Option B is bypassed until the user enables its group (it would render a second, paid video).
        option_b = [node for node in workflow["nodes"] if node["pos"][1] >= 740]
        self.assertEqual(len(option_b), 4)
        self.assertEqual({node["mode"] for node in option_b}, {4})
        self.assertEqual(
            {node["mode"] for node in workflow["nodes"] if node["pos"][1] < 740}, {0}
        )
        for node in workflow["nodes"]:
            if node["type"] == "BytePlusSeedance2Reference":
                self.assertIn("asset1", node["widgets_values"][1])

    def test_virtual_portrait_new_asset_template(self):
        workflow = load_workflow("Virtual Portrait - New Asset.json")
        # The image becomes a virtual portrait asset, and its ID is the Seedance reference.
        self.assertEqual(self.edges(workflow), {
            ("LoadImage", 0, "BytePlusCreateImageAsset", "image"),
            ("BytePlusCreateImageAsset", 0, "BytePlusSeedance2Reference", self.ASSET_SLOT),
            ("BytePlusCreateImageAsset", 3, "PreviewAny", "source"),
            ("BytePlusSeedance2Reference", 0, "SaveVideo", "video"),
        })
        asset = next(n for n in workflow["nodes"] if n["type"] == "BytePlusCreateImageAsset")
        # group_id empty -> the "ComfyUI Virtual Portraits" group is found or created; wait until Active.
        self.assertEqual(asset["widgets_values"], ["", "", "ComfyUI Virtual Portraits", "My portrait", "default", True])
        reference = next(n for n in workflow["nodes"] if n["type"] == "BytePlusSeedance2Reference")
        self.assertIn("asset1", reference["widgets_values"][1])

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

    # Nodes ComfyUI runs on their own; every other node runs only when one of these uses its output.
    OUTPUT_NODE_TYPES = {
        "SaveImage", "PreviewImage", "SaveVideo", "PreviewAny", "PreviewAudio", "SaveAudio",
        "ImageCompare",
    }

    def test_every_node_leads_to_an_output_node(self):
        # A node that feeds no output node never runs, and a template without one fails
        # with "Prompt has no outputs".
        for name in sorted(EXPECTED_WORKFLOWS):
            workflow = load_workflow(name)
            types = {node["id"]: node["type"] for node in workflow["nodes"]}
            consumers = {}
            for _link_id, origin, _slot, target, _target_slot, _type in workflow["links"]:
                consumers.setdefault(origin, set()).add(target)
            runs = {node_id for node_id, node_type in types.items() if node_type in self.OUTPUT_NODE_TYPES}
            grew = True
            while grew:
                grew = False
                for node_id in types:
                    if node_id not in runs and consumers.get(node_id, set()) & runs:
                        runs.add(node_id)
                        grew = True
            with self.subTest(workflow=name):
                self.assertEqual(sorted(types[node_id] for node_id in set(types) - runs), [])

    def test_templates_do_not_embed_keys(self):
        # Workflows travel (and are embedded in output metadata): no key-like values in them.
        import re

        # A known key prefix, or a long run of letters and digits with no spaces or slashes.
        key_like = re.compile(r"^(ark-|AKLT|AKID)|^(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9_-]{32,}$")
        for name in sorted(EXPECTED_WORKFLOWS):
            for node in load_workflow(name)["nodes"]:
                for value in node.get("widgets_values") or []:
                    if isinstance(value, str):
                        with self.subTest(workflow=name, node=node["type"]):
                            self.assertIsNone(key_like.search(value.strip()), value[:12])

    def test_templates_have_no_client_inputs(self):
        # Nodes take the keys from Settings > BytePlus: no client sockets, no keys in workflows.
        for name in sorted(EXPECTED_WORKFLOWS):
            for node in load_workflow(name)["nodes"]:
                with self.subTest(workflow=name, node=node["type"]):
                    names = {item["name"] for item in node.get("inputs", [])}
                    self.assertFalse(names & {"client", "speech_client", "mediakit_client"})


if __name__ == "__main__":
    unittest.main()
