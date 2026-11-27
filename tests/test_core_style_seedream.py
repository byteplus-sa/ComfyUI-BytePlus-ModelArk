import base64
import importlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest import mock


# Needs a ComfyUI checkout and a Python env with torch and the BytePlus SDK:
#   COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_core_style_seedream
COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
requires_comfyui = unittest.skipUnless(
    COMFY_ROOT, "Set COMFYUI_ROOT to a ComfyUI checkout to run these tests."
)

if COMFY_ROOT:
    if COMFY_ROOT not in sys.path:
        sys.path.insert(0, COMFY_ROOT)

    # ComfyUI owns the top-level ``utils`` package. Preload it from ComfyUI so a
    # ``utils`` module elsewhere on sys.path cannot shadow it.
    import utils  # noqa: F401, E402
    import comfy.cli_args  # noqa: E402

    # Importing comfy.model_management probes CUDA unless this is set; the tests need no GPU
    # (CI installs CPU-only torch).
    comfy.cli_args.args.cpu = True

    PACKAGE_NAME = "byteplus_plugin_test"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [PLUGIN_ROOT]
        sys.modules[PACKAGE_NAME] = package

    models_config = importlib.import_module(f"{PACKAGE_NAME}.nodes.models_config")
    executor = importlib.import_module(f"{PACKAGE_NAME}.nodes.executor")
    seedream_utils = importlib.import_module(f"{PACKAGE_NAME}.nodes.seedream_utils")
    nodes_seedream = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_seedream")


def setUpModule():
    # Hide the tester's own BYTEPLUS_* variables and user/.env (see tests/support.py).
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


PRO = "seedream 5.0 pro"
FLASH = "seedream 5.0 flash"
LITE = "seedream 5.0 lite"
V45 = "seedream-4-5-251128"
V40 = "seedream-4-0-250828"
V55 = "seedream 5.5"

# Input ids per model option, as core orders them, then this pack's extras.
EXPECTED_OPTION_INPUTS = {
    PRO: [
        "size_preset", "width", "height", "images", "prompt_optimization", "seed",
        "watermark", "thinking",
        "generation_count", "output_format", "background", "reference_mask",
    ],
    FLASH: [
        "size_preset", "width", "height", "images", "seed", "watermark",
        "generation_count", "output_format", "background", "reference_mask",
    ],
    LITE: [
        "size_preset", "width", "height", "max_images", "images", "fail_on_partial",
        "seed", "watermark", "thinking",
        "generation_count",
    ],
    V55: [
        "size_preset", "width", "height", "background", "images", "seed", "watermark",
        "prompt_optimization", "generation_count", "output_format", "safety_identifier",
    ],
}
EXPECTED_OPTION_INPUTS[V45] = EXPECTED_OPTION_INPUTS[LITE]
EXPECTED_OPTION_INPUTS[V40] = EXPECTED_OPTION_INPUTS[LITE]
EXTRAS = {"generation_count", "output_format", "background", "reference_mask"}

LAYER_OPTION_INPUTS = {
    PRO: [
        "image", "prompt", "size", "seed", "prompt_optimization", "watermark", "crop_layers",
        "output_format", "save_layers", "filename_prefix",
    ],
    FLASH: [
        "image", "prompt", "size", "seed", "watermark", "crop_layers",
        "output_format", "save_layers", "filename_prefix",
    ],
    V55: [
        "image", "prompt", "size", "max_images", "seed", "watermark", "crop_layers",
        "prompt_optimization", "output_format", "save_layers", "filename_prefix", "text_recognition",
        "safety_identifier",
    ],
}


def assert_matches_sdk(method, kwargs):
    """Bind request kwargs to the real SDK signature (fakes alone accept anything)."""
    import inspect

    fn = inspect.unwrap(method)
    for cell in fn.__closure__ or ():
        if inspect.isfunction(cell.cell_contents) and cell.cell_contents.__name__ == fn.__name__:
            fn = cell.cell_contents
    inspect.signature(fn).bind(None, **kwargs)


def png_b64(size, color):
    import PIL.Image

    buffer = io.BytesIO()
    PIL.Image.new("RGBA", size, color).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def decode_data_uri(uri):
    import PIL.Image

    header, data = uri.split(",", 1)
    return header, PIL.Image.open(io.BytesIO(base64.b64decode(data)))


def option_inputs(schema, key, combo_id="model"):
    combo = next(item for item in schema.inputs if item.id == combo_id)
    option = next(option for option in combo.options if option.key == key)
    return {item.id: item for item in option.inputs}, [item.id for item in option.inputs]


class FakeClient:
    """BytePlusClients stand-in with a fake Ark client."""

    def __init__(self, images):
        self.ark = SimpleNamespace(images=images)


class FakeImages:
    """images.generate: URL response, or a stream of events when stream=True."""

    def __init__(self, stream_events=None, url_count=1):
        from byteplussdkarkruntime.resources.images.images import Images as SdkImages

        self._sdk_generate = SdkImages.generate
        self.calls = []
        self.stream_events = stream_events
        self.url_count = url_count

    def generate(self, **kwargs):
        assert_matches_sdk(self._sdk_generate, kwargs)
        self.calls.append(dict(kwargs))
        if kwargs.get("stream"):
            events = self.stream_events
            if events is None:
                events = default_stream_events(kwargs)
            return iter(events)
        return SimpleNamespace(
            model=kwargs["model"],
            created=1,
            data=[
                SimpleNamespace(url=f"https://example.invalid/image{i}.png")
                for i in range(self.url_count)
            ],
        )


def completed_event(model, generated=1):
    from byteplussdkarkruntime.types.images.image_gen_completed_event import Usage

    # A real SDK model: json.dumps of the response output must handle it.
    usage = Usage(generated_images=generated, output_tokens=16, total_tokens=16)
    return SimpleNamespace(type="image_generation.completed", usage=usage, model=model, created=1)


def image_event(index, size=(8, 8)):
    return SimpleNamespace(
        type="image_generation.partial_succeeded",
        b64_json=png_b64(size, (0, 0, 255, 255)),
        image_index=index,
        error=None,
        size=f"{size[0]}x{size[1]}",
    )


def failed_event(index, code="OutputImageSensitiveContentDetected"):
    return SimpleNamespace(
        type="image_generation.partial_failed",
        image_index=index,
        error=SimpleNamespace(code=code, message="output image may contain sensitive information"),
    )


def default_stream_events(kwargs):
    options = kwargs.get("sequential_image_generation_options")
    count = options.max_images if options is not None else 1
    return [image_event(i) for i in range(count)] + [completed_event(kwargs["model"], count)]


BYTEPLUS_FIELD_DEVIATIONS = {
    **{
        ("BytePlusSeedream", model, side): {"min": minimum, "max": maximum}
        for model, minimum, maximum in (
            (PRO, 240, 8600), (FLASH, 240, 8600), (LITE, 480, 16384),
            (V45, 480, 16384), (V40, 240, 16384),
        )
        for side in ("width", "height")
    },
    **{
        ("BytePlusSeedream", V55, side): {"max": 11520}
        for side in ("width", "height")
    },
    **{
        ("BytePlusSeedream", model, "max_images"): {"max": 15}
        for model in (LITE, V45, V40)
    },
    **{
        ("BytePlusSeedream", model, "images"): {
            "tooltip": "Optional reference image(s) for image-to-image or multi-reference generation. Up to 14 images.",
        }
        for model in (V45, V40)
    },
    ("BytePlusSeedream", V55, "size_preset"): {"default": "2K (adaptive)"},
    ("BytePlusSeedream", V55, "background"): {
        "tooltip": "auto: choose from the prompt (JPEG is always opaque). transparent: generate "
        "a PNG with transparency from text-to-image or up to 10 references, also "
        "returned as the mask output. opaque: generate without transparency.",
    },
    **{
        (node, V55, "seed"): {
            "tooltip": "Change this value to run again. A generation seed is not set for Seedream 5.5.",
        }
        for node in ("BytePlusSeedream", "BytePlusSeedreamLayerSeparation")
    },
    **{
        ("BytePlusSeedreamLayerSeparation", model, name): {"tooltip": tooltip}
        for model in (PRO, FLASH, V55)
        for name, tooltip in (
            ("image", "The image to separate. Exactly one image, at least 262,144 pixels (e.g. 512x512), aspect ratio "
             "between 1:16 and 16:1. Inputs larger than about 4MP are downscaled before upload."),
            ("prompt", "How to separate the image. Leave empty to auto-detect and separate all major elements. "
             "Describe elements in natural language to control the separation, or target exact regions "
             "with <bbox>left top right bottom</bbox> tags (coordinates 0-999, relative to the image)."),
        )
    },
}
ADAPTIVE_PRESETS = {
    PRO: ["1K (adaptive)", "1.5K (adaptive)", "2K (adaptive)"],
    FLASH: ["1K (adaptive)", "1.5K (adaptive)", "2K (adaptive)"],
    LITE: ["2K (adaptive)", "3K (adaptive)", "4K (adaptive)"],
    V45: ["2K (adaptive)", "4K (adaptive)"],
    V40: ["1K (adaptive)", "2K (adaptive)", "4K (adaptive)"],
    V55: ["1K (adaptive)", "1.5K (adaptive)", "2K (adaptive)"],
}
PRO_EXTRA_PRESETS = [
    "(1.5K) 1536x1536 (1:1)", "(1.5K) 1344x1792 (3:4)",
    "(1.5K) 1792x1344 (4:3)", "(1.5K) 2048x1152 (16:9)",
    "(1.5K) 1152x2048 (9:16)", "(1.5K) 1248x1872 (2:3)",
    "(1.5K) 1872x1248 (3:2)", "(1.5K) 2352x1008 (21:9)",
]
# BytePlus 5.5 high-resolution update supplied 2026-11-27, beyond core's presets.
V55_EXTRA_PRESETS = [
    ("(4K) 3840x2160 (16:9)", 3840, 2160),
    ("(4K) 2160x3840 (9:16)", 2160, 3840),
]


@requires_comfyui
class SeedreamSchemaTests(unittest.TestCase):
    def test_top_level_order_and_outputs(self):
        schema = nodes_seedream.BytePlusSeedream.define_schema()
        self.assertEqual(schema.node_id, "BytePlusSeedream")
        self.assertEqual(schema.display_name, "BytePlus Seedream")
        self.assertEqual([item.id for item in schema.inputs], ["prompt", "model"])
        self.assertEqual(schema.inputs[1].io_type, "COMFY_DYNAMICCOMBO_V3")
        self.assertEqual(
            [option.key for option in schema.inputs[1].options], [PRO, FLASH, LITE, V45, V40, V55]
        )
        self.assertEqual(
            [(output.io_type, output.display_name) for output in schema.outputs],
            [("IMAGE", None), ("STRING", "response"), ("MASK", "mask")],
        )
        self.assertFalse(schema.is_api_node)

    def test_option_input_order_extras_last_and_advanced(self):
        schema = nodes_seedream.BytePlusSeedream.define_schema()
        for model, expected in EXPECTED_OPTION_INPUTS.items():
            with self.subTest(model=model):
                inputs, ids = option_inputs(schema, model)
                self.assertEqual(ids, expected)
                for name in ("generation_count", "output_format"):
                    if name in inputs:
                        self.assertTrue(inputs[name].advanced, name)
                if "background" in inputs:
                    self.assertEqual(bool(inputs["background"].advanced), model != V55)
                for name in ("fail_on_partial", "prompt_optimization", "watermark", "thinking"):
                    if name in inputs:
                        self.assertTrue(inputs[name].advanced, name)
                for name in ("size_preset", "width", "height", "seed"):
                    if name in inputs:
                        self.assertFalse(inputs[name].advanced, name)
                seed = inputs["seed"]
                self.assertEqual((seed.default, seed.min, seed.max), (42, 0, 2147483647))
                self.assertTrue(seed.control_after_generate)
                if model != V55:
                    self.assertEqual(seed.tooltip, "Seed to use for generation.")
                self.assertFalse(inputs["watermark"].default)
                self.assertEqual(
                    inputs["watermark"].tooltip,
                    'Whether to add an "AI generated" watermark to the image.',
                )
                if "thinking" in inputs:
                    self.assertTrue(inputs["thinking"].default)
                if "fail_on_partial" in inputs:
                    self.assertFalse(inputs["fail_on_partial"].default)

    def test_per_model_caps_and_presets(self):
        schema = nodes_seedream.BytePlusSeedream.define_schema()
        caps = {
            # (refs, custom side min, custom side max, max_images): sides follow the
            # BytePlus pixel range and the 1:16-16:1 aspect limit.
            PRO: (10, 240, 8600, None),
            FLASH: (10, 240, 8600, None),
            LITE: (14, 480, 16384, 15),
            V45: (14, 480, 16384, 15),
            V40: (14, 240, 16384, 15),
        }
        first_presets = {
            PRO: "(1K) 1024x1024 (1:1)",
            FLASH: "(1K) 1024x1024 (1:1)",
            LITE: "(2K) 2048x2048 (1:1)",
            V45: "(2K) 2048x2048 (1:1)",
            V40: "(1K) 1024x1024 (1:1)",
        }
        adaptive = {
            PRO: ["1K (adaptive)", "1.5K (adaptive)", "2K (adaptive)"],
            FLASH: ["1K (adaptive)", "1.5K (adaptive)", "2K (adaptive)"],
            LITE: ["2K (adaptive)", "3K (adaptive)", "4K (adaptive)"],
            V45: ["2K (adaptive)", "4K (adaptive)"],
            V40: ["1K (adaptive)", "2K (adaptive)", "4K (adaptive)"],
        }
        for model, (max_refs, min_side, max_side, max_images) in caps.items():
            with self.subTest(model=model):
                inputs, _ids = option_inputs(schema, model)
                names = inputs["images"].template.names
                self.assertEqual(names, [f"image_{i}" for i in range(1, max_refs + 1)])
                self.assertEqual(inputs["images"].template.min, 0)
                self.assertEqual((inputs["width"].min, inputs["width"].max, inputs["width"].step), (min_side, max_side, 2))
                self.assertEqual((inputs["height"].min, inputs["height"].max), (min_side, max_side))
                self.assertEqual(inputs["width"].default, 2048)
                if max_images is None:
                    self.assertNotIn("max_images", inputs)
                else:
                    self.assertEqual(inputs["max_images"].max, max_images)
                    self.assertEqual(inputs["max_images"].default, 1)
                options = inputs["size_preset"].options
                self.assertEqual(options[0], first_presets[model])
                self.assertEqual(inputs["size_preset"].default, first_presets[model])
                self.assertEqual(options[-1], "Custom")
                self.assertEqual(options[-1 - len(adaptive[model]):-1], adaptive[model])
        _inputs, _ = option_inputs(schema, FLASH)
        self.assertIn("(1.5K) 2048x1152 (16:9)", _inputs["size_preset"].options)
        # 5.0 Pro also has fixed 1.5K sizes (BytePlus docs, ModelArk console), between 1K and 2K.
        pro_options = option_inputs(schema, PRO)[0]["size_preset"].options
        tiers = [label.split(")")[0] + ")" for label in pro_options if label.startswith("(")]
        self.assertEqual(tiers, ["(1K)"] * 8 + ["(1.5K)"] * 8 + ["(2K)"] * 8)
        self.assertIn("(1.5K) 1792x1344 (4:3)", pro_options)
        self.assertIn("(1.5K) 2352x1008 (21:9)", pro_options)
        lite, _ = option_inputs(schema, LITE)
        self.assertIn("(3K) 4704x2016 (21:9)", lite["size_preset"].options)
        self.assertIn("(4K) 6240x2656 (21:9)", lite["size_preset"].options)
        v45, _ = option_inputs(schema, V45)
        self.assertNotIn("(1K) 1024x1024 (1:1)", v45["size_preset"].options)

    def test_model_values_are_byteplus_ids(self):
        self.assertEqual(
            models_config.SEEDREAM_MODELS,
            {
                PRO: "dola-seedream-5-0-pro-260628",
                FLASH: "dola-seedream-5-0-flash-260915",
                LITE: "seedream-5-0-260128",
                V45: "seedream-4-5-251128",
                V40: "seedream-4-0-250828",
                V55: "dola-seedream-5-5-261028",
            },
        )
        self.assertEqual(
            models_config.SEEDREAM_LAYER_SEPARATION_MODELS,
            {PRO: "dola-seedream-5-0-pro-260628", FLASH: "dola-seedream-5-0-flash-260915",
             V55: "dola-seedream-5-5-261028"},
        )

    def test_55_schema_matches_documented_capabilities(self):
        schema = nodes_seedream.BytePlusSeedream.define_schema()
        inputs, _ = option_inputs(schema, V55)
        self.assertEqual(inputs["size_preset"].default, "2K (adaptive)")
        for size in ("1K (adaptive)", "1.5K (adaptive)", "2K (adaptive)", "Custom",
                     "(1K) 1360x768 (16:9)", "(2K) 2368x1776 (4:3)"):
            self.assertIn(size, inputs["size_preset"].options)
        for label, _, _ in V55_EXTRA_PRESETS:
            self.assertIn(label, inputs["size_preset"].options)
        self.assertNotIn("4K (adaptive)", inputs["size_preset"].options)
        for side in ("width", "height"):
            self.assertEqual((inputs[side].min, inputs[side].max, inputs[side].step), (240, 11520, 2))
        self.assertEqual(len(inputs["images"].template.names), 10)
        self.assertEqual(inputs["output_format"].options, ["png", "jpeg"])
        self.assertEqual(inputs["output_format"].default, "png")
        self.assertEqual(inputs["background"].options, ["auto", "opaque", "transparent"])
        self.assertEqual(inputs["background"].default, "auto")
        self.assertEqual(inputs["prompt_optimization"].options, ["standard"])
        self.assertIn("text-to-image", inputs["background"].tooltip)
        self.assertIn("not set", inputs["seed"].tooltip)
        self.assertNotIn("seed", inputs["generation_count"].tooltip)
        for name in ("reference_mask", "thinking", "max_images"):
            self.assertNotIn(name, inputs)
        self.assertTrue(inputs["safety_identifier"].optional)
        self.assertTrue(inputs["safety_identifier"].advanced)
        layers, _ = option_inputs(nodes_seedream.BytePlusSeedreamLayerSeparation.define_schema(), V55)
        self.assertEqual((layers["max_images"].min, layers["max_images"].max, layers["max_images"].default),
                         (2, 100, 30))
        self.assertFalse(layers["text_recognition"].default)
        self.assertEqual(layers["prompt_optimization"].options, ["standard"])
        self.assertNotIn("background", layers)
        for model_inputs in (inputs, layers):
            self.assertTrue(model_inputs["prompt_optimization"].optional)
            self.assertTrue(model_inputs["prompt_optimization"].advanced)
        self.assertFalse(layers["max_images"].optional)
        self.assertFalse(layers["max_images"].advanced)
        self.assertEqual(layers["max_images"].step, 1)
        self.assertEqual(layers["max_images"].display_mode.value, "number")

    def test_layer_separation_schema(self):
        schema = nodes_seedream.BytePlusSeedreamLayerSeparation.define_schema()
        self.assertEqual(schema.node_id, "BytePlusSeedreamLayerSeparation")
        self.assertEqual(schema.display_name, "BytePlus Seedream Layer Separation")
        self.assertEqual([item.id for item in schema.inputs], ["model"])
        for model, expected in LAYER_OPTION_INPUTS.items():
            with self.subTest(model=model):
                inputs, ids = option_inputs(schema, model)
                self.assertEqual(ids, expected)
                self.assertEqual(inputs["size"].options, ["auto", "1K", "1.5K", "2K"])
                self.assertEqual(inputs["seed"].default, 42)
                self.assertTrue(inputs["seed"].control_after_generate)
                self.assertFalse(inputs["crop_layers"].advanced)
                self.assertEqual(inputs["crop_layers"].default, model == V55)
                self.assertEqual(
                    (inputs["crop_layers"].label_on, inputs["crop_layers"].label_off),
                    ("minimal size", "full canvas"),
                )
                for name in ("watermark", "output_format", "save_layers", "filename_prefix"):
                    self.assertTrue(inputs[name].advanced, name)
                self.assertFalse(inputs["save_layers"].default)
        self.assertEqual(
            [(output.io_type, output.display_name) for output in schema.outputs],
            [
                ("IMAGE", "base_image"), ("MASK", "base_mask"), ("IMAGE", "layers"),
                ("MASK", "masks"), ("BOUNDING_BOX", "bboxes"), ("LAYERS", "layer_stack"),
                ("STRING", "layers_json"),
            ],
        )
        self.assertFalse(schema.is_output_node)


    def test_documented_byteplus_limits(self):
        """Where BytePlus documents other limits than core uses, ours follow BytePlus."""
        schema = nodes_seedream.BytePlusSeedream.define_schema()
        options = {o.key: o for o in schema.inputs[1].options}
        for key, refs in ((LITE, 14), (V45, 14), (V40, 14), (PRO, 10), (FLASH, 10)):
            inputs = {i.id: i for i in options[key].inputs}
            self.assertEqual(len(inputs["images"].template.names), refs, key)
            if "max_images" in inputs:
                self.assertEqual(inputs["max_images"].max, 15, key)
        layers = nodes_seedream.BytePlusSeedreamLayerSeparation.define_schema()
        for option in layers.inputs[0].options:
            inputs = {i.id: i for i in option.inputs}
            self.assertIn("262,144 pixels", inputs["image"].tooltip)
            self.assertIn("0-999", inputs["prompt"].tooltip)

    def core_node_pairs(self):
        try:
            core = importlib.import_module("comfy_api_nodes.nodes_bytedance")
            return [
                (core.ByteDanceSeedreamNodeV3, nodes_seedream.BytePlusSeedream),
                (
                    core.ByteDanceSeedreamLayerSeparationNodeV2,
                    nodes_seedream.BytePlusSeedreamLayerSeparation,
                ),
            ]
        except Exception as exc:  # core API nodes unavailable in this checkout
            self.skipTest(f"comfy_api_nodes.nodes_bytedance not importable: {exc}")

    def assert_shared_input_matches_core(self, core_item, our_item, node_id, model=None):
        expected = deepcopy(core_item.as_dict())
        actual = our_item.as_dict()
        if model == V55 and core_item.id == "seed":
            self.assertEqual(core_item.tooltip, "Seed to use for generation.")
        expected.update(BYTEPLUS_FIELD_DEVIATIONS.get((node_id, model, core_item.id), {}))
        if core_item.io_type == "COMFY_DYNAMICCOMBO_V3":
            expected.pop("options")
            actual.pop("options")
        if node_id == "BytePlusSeedream":
            if core_item.id == "size_preset":
                options = list(expected["options"])
                self.assertEqual(options[-1], "Custom")
                if model == PRO:
                    options[8:8] = PRO_EXTRA_PRESETS
                elif model == V55:
                    options[-1:-1] = [label for label, _, _ in V55_EXTRA_PRESETS]
                expected["options"] = options[:-1] + ADAPTIVE_PRESETS[model] + options[-1:]
            if core_item.id == "images" and model in (V45, V40):
                expected["template"]["names"] = [f"image_{index}" for index in range(1, 15)]
        self.assertEqual(
            (core_item.id, core_item.io_type, expected),
            (our_item.id, our_item.io_type, actual),
            f"{node_id}: {model}: {core_item.id}",
        )

    def assert_schema_matches_core(self, core_schema, our_schema):
        self.assertEqual(core_schema.description, our_schema.description)
        self.assertEqual(core_schema.is_output_node, our_schema.is_output_node)
        self.assertEqual([item.id for item in core_schema.inputs], [item.id for item in our_schema.inputs])
        for core_input, our_input in zip(core_schema.inputs, our_schema.inputs, strict=True):
            self.assert_shared_input_matches_core(core_input, our_input, our_schema.node_id)
            if core_input.io_type != "COMFY_DYNAMICCOMBO_V3":
                continue
            core_keys = [option.key for option in core_input.options]
            self.assertIn(V55, core_keys)
            self.assertEqual(
                [option.key for option in our_input.options],
                [key for key in core_keys if key != V55] + [V55],
            )
            our_options = {option.key: option for option in our_input.options}
            for core_option in core_input.options:
                our_option = our_options[core_option.key]
                if our_schema.node_id == "BytePlusSeedream":
                    extras = EXTRAS if core_option.key != V55 else {
                        "prompt_optimization", "generation_count", "output_format", "safety_identifier",
                    }
                    if core_option.key not in (PRO, FLASH, V55):
                        extras = {"generation_count"}
                else:
                    extras = {"output_format", "save_layers", "filename_prefix"}
                    if core_option.key == V55:
                        extras |= {"prompt_optimization", "text_recognition", "safety_identifier"}
                core_ids = [item.id for item in core_option.inputs]
                self.assertFalse(set(core_ids) & extras)
                self.assertEqual(
                    [item.id for item in our_option.inputs[:len(core_ids)]], core_ids, core_option.key
                )
                extra_ids = [item.id for item in our_option.inputs[len(core_ids):]]
                self.assertEqual(set(extra_ids), extras)
                self.assertEqual(len(extra_ids), len(extras))
                for core_item, our_item in zip(core_option.inputs, our_option.inputs, strict=False):
                    self.assert_shared_input_matches_core(
                        core_item, our_item, our_schema.node_id, core_option.key
                    )
        self.assertGreaterEqual(len(our_schema.outputs), len(core_schema.outputs))
        for core_output, our_output in zip(core_schema.outputs, our_schema.outputs, strict=False):
            self.assertEqual(
                (core_output.io_type, core_output.as_dict()),
                (our_output.io_type, our_output.as_dict()),
            )

    def test_matches_core_schema(self):
        for core_node, our_node in self.core_node_pairs():
            with self.subTest(node=our_node.__name__):
                self.assert_schema_matches_core(core_node.define_schema(), our_node.define_schema())

    def test_matches_core_schema_rejects_unrelated_input_changes(self):
        for core_node, our_node in self.core_node_pairs():
            schema = core_node.define_schema()
            combo = next(item for item in schema.inputs if item.id == "model")
            for option in combo.options:
                for name, field, value in (
                    ("seed", "default", 99), ("watermark", "advanced", False),
                    ("images", "tooltip", "Changed reference requirements"), ("size", "default", "2K"),
                    ("max_images", "max", 101), ("background", "default", "opaque"),
                ):
                    if name not in {item.id for item in option.inputs}:
                        continue
                    deviations = BYTEPLUS_FIELD_DEVIATIONS.get((our_node.__name__, option.key, name), {})
                    if field in deviations:
                        continue
                    with self.subTest(node=our_node.__name__, model=option.key, input=name, field=field):
                        changed = deepcopy(schema)
                        setattr(option_inputs(changed, option.key)[0][name], field, value)
                        with self.assertRaises(AssertionError):
                            self.assert_schema_matches_core(changed, our_node.define_schema())

    def test_matches_core_schema_rejects_unknown_models_and_inputs(self):
        for core_node, our_node in self.core_node_pairs():
            for change in ("model", "input"):
                with self.subTest(node=our_node.__name__, change=change):
                    schema = deepcopy(core_node.define_schema())
                    combo = next(item for item in schema.inputs if item.id == "model")
                    if change == "model":
                        combo.options.append(nodes_seedream.comfy_io.DynamicCombo.Option("unknown model", []))
                    else:
                        combo.options[0].inputs.append(nodes_seedream.comfy_io.Boolean.Input("unexpected"))
                    with self.assertRaises(AssertionError):
                        self.assert_schema_matches_core(schema, our_node.define_schema())

    def test_matches_core_schema_rejects_changed_presets(self):
        core_node, our_node = self.core_node_pairs()[0]
        schema = core_node.define_schema()
        combo = next(item for item in schema.inputs if item.id == "model")
        for option in combo.options:
            with self.subTest(model=option.key):
                changed = deepcopy(schema)
                option_inputs(changed, option.key)[0]["size_preset"].options.append("(8K) 8192x8192 (1:1)")
                with self.assertRaises(AssertionError):
                    self.assert_schema_matches_core(changed, our_node.define_schema())

    def test_matches_core_schema_checks_55_seed_tooltip(self):
        for core_node, our_node in self.core_node_pairs():
            changed = deepcopy(core_node.define_schema())
            option_inputs(changed, V55)[0]["seed"].tooltip = "Unexpected seed guidance"
            with self.subTest(node=our_node.__name__), self.assertRaises(AssertionError):
                self.assert_schema_matches_core(changed, our_node.define_schema())

    def test_55_fixed_presets_match_core_plus_uhd_and_resolve_to_exact_pixels(self):
        core_api = importlib.import_module("comfy_api_nodes.apis.bytedance")
        presets = core_api.RECOMMENDED_PRESETS_SEEDREAM_5_5[:-1] + V55_EXTRA_PRESETS
        self.assertEqual(seedream_utils.SEEDREAM_PRESETS[V55], presets)
        for label, width, height in presets:
            with self.subTest(preset=label):
                self.assertEqual(
                    nodes_seedream.resolve_seedream_size(V55, label, 0, 0), f"{width}x{height}"
                )

    def test_55_parity_rejects_mutated_shared_fields(self):
        mutations = {
            "BytePlusSeedream": [
                ("width", "min", 242), ("width", "max", 9000), ("width", "default", 1024),
                ("height", "max", 9000), ("height", "step", 1),
                ("size_preset", "default", "(2K) 2048x2048 (1:1)"),
                ("size_preset", "options", ["Custom"]),
                ("background", "options", ["auto", "transparent", "opaque"]),
                ("background", "options", ["auto", "opaque", "transparent", "invalid"]),
                ("background", "default", "opaque"), ("background", "advanced", True),
                ("seed", "max", 100), ("seed", "default", 43),
                ("images", "optional", True), ("watermark", "default", True),
            ],
            "BytePlusSeedreamLayerSeparation": [
                ("max_images", "min", 1), ("max_images", "max", 99),
                ("max_images", "default", 31), ("max_images", "step", 2),
                ("max_images", "optional", True), ("max_images", "advanced", True),
                ("max_images", "tooltip", "Different billing wording"),
                ("max_images", "display_mode", nodes_seedream.comfy_io.NumberDisplay.slider),
                ("crop_layers", "default", False), ("size", "default", "1K"),
                ("size", "options", ["auto", "1K", "2K"]),
                ("size", "options", ["auto", "1K", "1.5K", "2K", "4K"]),
                ("image", "optional", True), ("prompt", "default", "split"),
                ("seed", "max", 100), ("seed", "control_after_generate", False),
            ],
        }
        for core_node, our_node in self.core_node_pairs():
            core_schema = core_node.define_schema()
            self.assert_schema_matches_core(core_schema, our_node.define_schema())
            for name, field, value in mutations[our_node.__name__]:
                with self.subTest(node=our_node.__name__, input=name, field=field, value=value):
                    mutated = our_node.define_schema()
                    setattr(option_inputs(mutated, V55)[0][name], field, value)
                    with self.assertRaises(AssertionError):
                        self.assert_schema_matches_core(core_schema, mutated)

    def test_55_parity_rejects_changed_shared_inputs_and_model_order(self):
        for core_node, our_node in self.core_node_pairs():
            core_schema = core_node.define_schema()
            self.assert_schema_matches_core(core_schema, our_node.define_schema())
            for mutation in ("missing", "extra", "reordered", "missing_model", "model_order"):
                with self.subTest(node=our_node.__name__, mutation=mutation):
                    mutated = our_node.define_schema()
                    combo = next(item for item in mutated.inputs if item.id == "model")
                    option = next(option for option in combo.options if option.key == V55)
                    if mutation == "missing":
                        option.inputs.pop(0)
                    elif mutation == "extra":
                        option.inputs.append(nodes_seedream.comfy_io.Boolean.Input("unexpected"))
                    elif mutation == "reordered":
                        option.inputs[0], option.inputs[1] = option.inputs[1], option.inputs[0]
                    elif mutation == "missing_model":
                        combo.options.remove(option)
                    else:
                        combo.options[0], combo.options[1] = combo.options[1], combo.options[0]
                    with self.assertRaises(AssertionError):
                        self.assert_schema_matches_core(core_schema, mutated)

    def test_55_parity_rejects_changed_reference_template(self):
        core_node, our_node = self.core_node_pairs()[0]
        core_schema = core_node.define_schema()
        self.assert_schema_matches_core(core_schema, our_node.define_schema())
        for field, value in (("names", ["image_1"]), ("min", 1)):
            with self.subTest(field=field):
                mutated = our_node.define_schema()
                template = deepcopy(option_inputs(mutated, V55)[0]["images"].template)
                setattr(template, field, value)
                option_inputs(mutated, V55)[0]["images"].template = template
                with self.assertRaises(AssertionError):
                    self.assert_schema_matches_core(core_schema, mutated)


@requires_comfyui
class SeedreamRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import torch

        self._old = (
            seedream_utils.download_url_to_image_tensor_async,
            seedream_utils.download_url_to_rgba_tensor_async,
            getattr(executor.PromptServer, "instance", None),
            getattr(nodes_seedream.BytePlusSeedream, "hidden", None),
        )

        async def fake_rgb(_session, _url):
            return torch.zeros((1, 4, 4, 3))

        async def fake_rgba(_session, _url):
            tensor = torch.ones((1, 4, 4, 4))
            tensor[..., 3] = 0.25
            return tensor

        seedream_utils.download_url_to_image_tensor_async = fake_rgb
        seedream_utils.download_url_to_rgba_tensor_async = fake_rgba
        executor.PromptServer.instance = SimpleNamespace()
        nodes_seedream.BytePlusSeedream.hidden = SimpleNamespace(unique_id="test-node", prompt={})

    def tearDown(self):
        seedream_utils.download_url_to_image_tensor_async = self._old[0]
        seedream_utils.download_url_to_rgba_tensor_async = self._old[1]
        if self._old[2] is None:
            delattr(executor.PromptServer, "instance")
        else:
            executor.PromptServer.instance = self._old[2]
        if self._old[3] is None:
            delattr(nodes_seedream.BytePlusSeedream, "hidden")
        else:
            nodes_seedream.BytePlusSeedream.hidden = self._old[3]

    async def run_node(self, model, prompt="a red apple", fake=None, **options):
        fake = fake or FakeImages()
        client = FakeClient(fake)
        result = await nodes_seedream.BytePlusSeedream.execute(
            client, prompt, {"model": model, **options}
        )
        return fake, client, result

    async def test_omitted_size_presets_keep_effective_defaults(self):
        for model, size in (
            (PRO, "1024x1024"), (FLASH, "1024x1024"), (LITE, "2048x2048"),
            (V45, "2048x2048"), (V40, "1024x1024"), (V55, "2K"),
        ):
            with self.subTest(model=model):
                default = option_inputs(nodes_seedream.BytePlusSeedream.define_schema(), model)[0]["size_preset"].default
                omitted, _, _ = await self.run_node(model)
                explicit, _, _ = await self.run_node(model, size_preset=default)
                self.assertEqual(omitted.calls[0]["size"], size)
                self.assertEqual(explicit.calls[0]["size"], size)

    async def test_pro_text_to_image_request(self):
        fake, client, result = await self.run_node(
            PRO, size_preset="(2K) 2848x1600 (16:9)", seed=42, watermark=True
        )
        request = fake.calls[0]
        self.assertEqual(request["model"], "dola-seedream-5-0-pro-260628")
        self.assertEqual(request["size"], "2848x1600")
        self.assertEqual(request["response_format"], "url")
        self.assertEqual(request["seed"], 42)
        self.assertTrue(request["watermark"])
        self.assertEqual(request["output_format"], "jpeg")
        self.assertEqual(request["optimize_prompt_options"].thinking, "enabled")
        self.assertIsNone(request["optimize_prompt_options"].mode)
        for key in ("image", "sequential_image_generation", "sequential_image_generation_options", "extra_body", "stream"):
            self.assertNotIn(key, request)
        image, response, mask = result
        self.assertEqual(tuple(image.shape), (1, 4, 4, 3))
        self.assertEqual(float(mask.sum()), 0.0)
        self.assertEqual(json.loads(response)[0]["urls"], ["https://example.invalid/image0.png"])

    async def test_55_transparent_text_to_image_matches_supplied_request(self):
        prompt = "Realistic embroidered patches on a transparent background."
        fake, _client, result = await self.run_node(
            V55, prompt=prompt, size_preset="2K (adaptive)",
            output_format="png", background="transparent", watermark=True,
        )
        self.assertEqual(fake.calls, [{
            "model": "dola-seedream-5-5-261028",
            "prompt": prompt,
            "size": "2K",
            "response_format": "url",
            "output_format": "png",
            "extra_body": {"background": "transparent"},
            "watermark": True,
            "optimize_prompt_options": fake.calls[0]["optimize_prompt_options"],
        }])
        self.assertEqual(fake.calls[0]["optimize_prompt_options"].mode, "standard")
        image, response, mask = result
        self.assertEqual(tuple(image.shape), (1, 4, 4, 3))
        self.assertEqual(tuple(mask.shape), (1, 4, 4))
        self.assertAlmostEqual(float(mask[0, 0, 0]), 0.75)
        self.assertEqual(json.loads(response)[0]["model"], "dola-seedream-5-5-261028")

    async def test_55_defaults_to_auto_png_preserves_alpha_and_ignores_pro_endpoint(self):
        with mock.patch.dict(os.environ, {"BYTEPLUS_SEEDREAM_5_0_PRO_ENDPOINT": "ep-image"}):
            fake, _client, result = await self.run_node(V55)
        request = fake.calls[0]
        self.assertEqual(request["model"], "dola-seedream-5-5-261028")
        self.assertEqual(request["size"], "2K")
        self.assertEqual(request["output_format"], "png")
        self.assertEqual(request["extra_body"], {"background": "auto"})
        self.assertEqual(request["optimize_prompt_options"].mode, "standard")
        for key in ("seed", "image", "stream", "sequential_image_generation"):
            self.assertNotIn(key, request)
        self.assertAlmostEqual(float(result[2].mean()), 0.75)

    async def test_55_rejects_invalid_options_before_submission(self):
        import torch

        cases = [
            ({"prompt": ""}, "Prompt cannot be empty"),
            ({"prompt": None}, "Prompt cannot be empty"),
            ({"size_preset": "4K (adaptive)"}, "size preset"),
            ({"size_preset": "Custom", "width": 2882, "height": 2880}, "Maximum image resolution"),
            ({"size_preset": "Custom", "width": 512, "height": 512}, "Minimum image resolution"),
            ({"size_preset": "Custom", "width": 0, "height": 2048}, "aspect ratio"),
            ({"size_preset": "Custom", "width": -2048, "height": -2048}, "aspect ratio"),
            ({"output_format": "jpeg", "background": "transparent"}, "set output_format to png"),
            ({"output_format": "gif"}, "output format"),
            ({"background": "invalid"}, "background"),
            ({"prompt_optimization": "fast"}, "prompt optimization"),
            ({"images": {"image_1": torch.ones((11, 16, 16, 3))}}, "reference images"),
            ({"images": {"image_1": torch.ones((1, 14, 16, 3))}}, "reference image"),
            ({"images": {"image_1": torch.empty((1, 6001, 6000, 3), device="meta")}}, "reference image"),
            ({"safety_identifier": "a" * 65}, "safety_identifier"),
            ({"safety_identifier": "\u00e9"}, "safety_identifier"),
            ({"safety_identifier": "user\nname"}, "safety_identifier"),
            ({"safety_identifier": False}, "safety_identifier"),
        ]
        for options, message in cases:
            with self.subTest(options=list(options)):
                fake = FakeImages()
                with self.assertRaisesRegex(Exception, message):
                    await self.run_node(V55, fake=fake, **options)
                self.assertEqual(fake.calls, [])

    async def test_55_parallel_generation_preserves_transparency(self):
        fake, _client, result = await self.run_node(
            V55, generation_count=2, background="transparent",
        )
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(tuple(result[0].shape), (2, 4, 4, 3))
        self.assertEqual(tuple(result[2].shape), (2, 4, 4))
        self.assertEqual(len(json.loads(result[1])), 2)
        self.assertAlmostEqual(float(result[2].mean()), 0.75)

    async def test_55_missing_url_is_reported(self):
        with self.assertRaisesRegex(Exception, "download"):
            await self.run_node(V55, fake=FakeImages(url_count=0))

    async def test_55_sdk_serializes_background_as_a_top_level_field(self):
        import httpx
        from byteplussdkarkruntime import Ark

        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            self.assertEqual(request.url.path, "/api/v3/images/generations")
            return httpx.Response(200, json={
                "model": "dola-seedream-5-5-261028",
                "created": 1,
                "data": [{"url": "https://example.invalid/image.png"}],
            })

        with Ark(
            api_key="test-only",
            base_url="https://example.invalid/api/v3",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        ) as ark:
            await self.run_node(V55, fake=ark.images, background="transparent", watermark=True)
        self.assertEqual(len(requests), 1)
        self.assertEqual({key: value for key, value in requests[0].items() if value is not None}, {
            "model": "dola-seedream-5-5-261028",
            "prompt": "a red apple",
            "size": "2K",
            "response_format": "url",
            "output_format": "png",
            "background": "transparent",
            "watermark": True,
            "optimize_prompt_options": {"mode": "standard", "thinking": None},
        })

    async def test_55_api_error_is_reported_without_retrying(self):
        class UnavailableImages(FakeImages):
            def generate(self, **kwargs):
                self.calls.append(kwargs)
                raise RuntimeError("Seedream 5.5 early-access entitlement required")

        fake = UnavailableImages()
        with self.assertRaisesRegex(Exception, "early-access entitlement required"):
            await self.run_node(V55, fake=fake)
        self.assertEqual(len(fake.calls), 1)

    async def test_55_documented_sizes_and_reference_editing(self):
        import torch

        for size in ("1K", "1.5K", "2K"):
            with self.subTest(size=size):
                fake, _, _ = await self.run_node(V55, size_preset=f"{size} (adaptive)")
                self.assertEqual(fake.calls[0]["size"], size)
        for width, height in ((1280, 720), (240, 3840), (2048, 1024), (2150, 2150)):
            fake, _, _ = await self.run_node(V55, size_preset="Custom", width=width, height=height)
            self.assertEqual(fake.calls[0]["size"], f"{width}x{height}")
        for count in (1, 10):
            fake, _, result = await self.run_node(
                V55, images={"image_1": torch.ones((count, 16, 16, 3))}, background="transparent",
            )
            images = fake.calls[0]["image"]
            images = [images] if isinstance(images, str) else images
            self.assertEqual(len(images), count)
            self.assertTrue(all(decode_data_uri(image)[1].mode == "RGB" for image in images))
            self.assertAlmostEqual(float(result[2].mean()), 0.75)

    async def test_55_uhd_presets_submit_explicit_dimensions(self):
        import torch

        for label, width, height in V55_EXTRA_PRESETS:
            for editing in (False, True):
                with self.subTest(preset=label, editing=editing):
                    refs = {"image_1": torch.ones((1, 16, 16, 3))} if editing else {}
                    fake, _, _ = await self.run_node(
                        V55, size_preset=label, width=0, height=0, images=refs,
                    )
                    self.assertEqual(len(fake.calls), 1)
                    request = fake.calls[0]
                    self.assertEqual(request["model"], "dola-seedream-5-5-261028")
                    self.assertEqual(request["size"], f"{width}x{height}")
                    self.assertEqual(request["response_format"], "url")
                    self.assertEqual(bool(request.get("image")), editing)

    async def test_55_custom_sizes_accept_new_pixel_and_aspect_boundaries(self):
        for width, height in (
            (3840, 2160), (2160, 3840), (2880, 2880),
            (11520, 720), (720, 11520),
        ):
            with self.subTest(width=width, height=height):
                self.assertEqual(width * height, 8_294_400)
                fake, _, _ = await self.run_node(
                    V55, size_preset="Custom", width=width, height=height,
                )
                self.assertEqual(fake.calls[0]["size"], f"{width}x{height}")

    async def test_55_custom_sizes_reject_excess_pixels_or_aspect_before_submission(self):
        for width, height, message in (
            (3840, 2162, "Maximum image resolution"),
            (2162, 3840, "Maximum image resolution"),
            (4096, 4096, "Maximum image resolution"),
            (11520, 722, "Maximum image resolution"),
            (11520, 718, "aspect ratio"),
            (718, 11520, "aspect ratio"),
        ):
            with self.subTest(width=width, height=height):
                fake = FakeImages()
                with self.assertRaisesRegex(nodes_seedream.BytePlusException, message):
                    await self.run_node(
                        V55, fake=fake, size_preset="Custom", width=width, height=height,
                    )
                self.assertEqual(fake.calls, [])

    async def test_55_uhd_does_not_expand_pro_or_flash_sizes(self):
        for model in (PRO, FLASH):
            for options in (
                {"size_preset": V55_EXTRA_PRESETS[0][0]},
                {"size_preset": "Custom", "width": 3840, "height": 2160},
            ):
                with self.subTest(model=model, options=options):
                    fake = FakeImages()
                    with self.assertRaises(nodes_seedream.BytePlusException):
                        await self.run_node(model, fake=fake, **options)
                    self.assertEqual(fake.calls, [])

    async def test_55_jpeg_auto_and_opaque_png_return_opaque_masks(self):
        for options in ({"output_format": "jpeg"}, {"background": "opaque"}):
            with self.subTest(options=options):
                _, _, result = await self.run_node(V55, **options)
                self.assertEqual(float(result[2].sum()), 0)

    async def test_55_safety_identifier_is_merged_with_background(self):
        fake, _, _ = await self.run_node(V55, safety_identifier="a" * 64)
        self.assertEqual(fake.calls[0]["extra_body"], {"background": "auto", "safety_identifier": "a" * 64})
        fake, _, _ = await self.run_node(V55, safety_identifier="")
        self.assertNotIn("safety_identifier", fake.calls[0]["extra_body"])

    async def test_55_omitted_optional_identifier_accepts_comfyui_none(self):
        fake, _, result = await self.run_node(V55, safety_identifier=None)
        self.assertNotIn("safety_identifier", fake.calls[0]["extra_body"])
        self.assertEqual(result[0].shape[0], 1)

    async def test_55_optional_prompt_optimization_none_uses_standard(self):
        fake, _, _ = await self.run_node(V55, prompt_optimization=None)
        self.assertEqual(fake.calls[0]["optimize_prompt_options"].mode, "standard")

    async def test_55_response_preserves_actual_background_metadata(self):
        class BackgroundImages(FakeImages):
            def generate(self, **kwargs):
                response = super().generate(**kwargs)
                response.data[0].background = "transparent"
                response.data[0].size = "2048x2048"
                response.data[0].output_format = "png"
                return response

        _, _, result = await self.run_node(V55, fake=BackgroundImages())
        self.assertEqual(json.loads(result[1])[0]["images"][0]["background"], "transparent")

    async def test_pro_endpoint_keeps_capabilities_and_leaves_flash_unchanged(self):
        with mock.patch.dict(os.environ, {"BYTEPLUS_SEEDREAM_5_0_PRO_ENDPOINT": "ep-image"}):
            fake, _, _ = await self.run_node(PRO, thinking=False, generation_count=2)
            self.assertEqual(len(fake.calls), 2)
            for request in fake.calls:
                self.assertEqual(request["model"], "ep-image")
                self.assertEqual(request["response_format"], "url")
                self.assertEqual(request["optimize_prompt_options"].thinking, "disabled")
            fake, _, _ = await self.run_node(FLASH)
            self.assertEqual(fake.calls[0]["model"], "dola-seedream-5-0-flash-260915")

    async def test_thinking_and_prompt_optimization_rules(self):
        import torch

        fake, _c, _r = await self.run_node(PRO, thinking=False)
        self.assertEqual(fake.calls[0]["optimize_prompt_options"].thinking, "disabled")

        # References: thinking is not sent; "fast" becomes mode=fast on Pro.
        refs = {"image_1": torch.ones((1, 8, 8, 3))}
        fake, _c, _r = await self.run_node(PRO, prompt_optimization="fast", images=refs)
        options = fake.calls[0]["optimize_prompt_options"]
        self.assertEqual((options.mode, options.thinking), ("fast", None))
        fake, _c, _r = await self.run_node(PRO, prompt_optimization="standard", **{"images": refs})
        self.assertNotIn("optimize_prompt_options", fake.calls[0])

        with self.assertRaisesRegex(Exception, "can only be disabled for text-to-image"):
            await self.run_node(PRO, thinking=False, **{"images": refs})

        # Flash has no thinking and no prompt_optimization.
        fake, _c, _r = await self.run_node(FLASH)
        self.assertNotIn("optimize_prompt_options", fake.calls[0])
        self.assertEqual(fake.calls[0]["model"], "dola-seedream-5-0-flash-260915")

        # Lite / 4.x: thinking for text-to-image only.
        fake, _c, _r = await self.run_node(V40)
        self.assertEqual(fake.calls[0]["optimize_prompt_options"].thinking, "enabled")
        fake, _c, _r = await self.run_node(V45, **{"images": refs})
        self.assertNotIn("optimize_prompt_options", fake.calls[0])

    async def test_references_are_sent_as_data_uris(self):
        import torch

        refs = {"image_2": torch.ones((1, 8, 16, 3)), "image_1": torch.zeros((2, 8, 8, 3))}
        fake, _c, _r = await self.run_node(PRO, **{"images": refs})
        image_param = fake.calls[0]["image"]
        self.assertEqual(len(image_param), 3)
        headers_and_images = [decode_data_uri(uri) for uri in image_param]
        self.assertTrue(all(h == "data:image/jpeg;base64" for h, _ in headers_and_images))
        # image_1 (a batch of two) comes before image_2
        self.assertEqual([img.size for _, img in headers_and_images], [(8, 8), (8, 8), (16, 8)])

        fake, _c, _r = await self.run_node(LITE, **{"images": {"image_1": torch.ones((1, 8, 8, 3))}})
        self.assertTrue(fake.calls[0]["image"].startswith("data:image/jpeg;base64,"))

    async def test_lite_streams_and_batches(self):
        fake, client, result = await self.run_node(
            LITE, size_preset="3K (adaptive)", max_images=3, seed=5
        )
        request = fake.calls[0]
        self.assertTrue(request["stream"])
        self.assertEqual(request["model"], "seedream-5-0-260128")
        self.assertEqual(request["size"], "3K")
        self.assertEqual(request["response_format"], "b64_json")
        self.assertEqual(request["sequential_image_generation"], "auto")
        self.assertEqual(request["sequential_image_generation_options"].max_images, 3)
        self.assertEqual(request["seed"], 5)
        image, response, mask = result
        self.assertEqual(tuple(image.shape), (3, 8, 8, 3))
        self.assertEqual(tuple(mask.shape), (3, 8, 8))
        self.assertEqual(json.loads(response)[0]["usage"]["generated_images"], 3)

        fake, _c, _r = await self.run_node(V40, size_preset="(1K) 1312x736 (16:9)")
        request = fake.calls[0]
        self.assertEqual(request["size"], "1312x736")
        pro_fake, _c, _r = await self.run_node(PRO, size_preset="(1.5K) 1792x1344 (4:3)")
        self.assertEqual(pro_fake.calls[0]["size"], "1792x1344")
        self.assertEqual(request["sequential_image_generation"], "disabled")
        self.assertNotIn("sequential_image_generation_options", request)

    async def test_lite_is_not_offered_in_eu_west_1(self):
        """Seedream 5.0 Lite was deactivated in eu-west-1: refused before any request."""
        fake = FakeImages()
        client = FakeClient(fake)
        client.region = "eu-west-1"
        with self.assertRaises(Exception) as ctx:
            await nodes_seedream.BytePlusSeedream.execute(client, "a red apple", {"model": LITE})
        self.assertIn("seedream-5-0-260128 is not available in eu-west-1", str(ctx.exception))
        self.assertTrue(str(ctx.exception).startswith("[BytePlus]"))
        self.assertEqual(fake.calls, [])

        # Other models in eu-west-1, and Lite in ap-southeast-1, run.
        for model in (PRO, FLASH, V45, V40):
            with self.subTest(model=model):
                await nodes_seedream.BytePlusSeedream.execute(client, "a red apple", {"model": model})
        self.assertEqual(len(fake.calls), 4)
        client.region = "ap-southeast-1"
        await nodes_seedream.BytePlusSeedream.execute(client, "a red apple", {"model": LITE})
        self.assertEqual(fake.calls[-1]["model"], "seedream-5-0-260128")


    async def test_custom_sizes_follow_byteplus(self):
        resolve = nodes_seedream.resolve_seedream_size
        # Valid BytePlus sizes below core's 1024 minimum side.
        self.assertEqual(resolve(PRO, "Custom", 1280, 720), "1280x720")
        self.assertEqual(resolve(FLASH, "Custom", 720, 1280), "720x1280")
        self.assertEqual(resolve(PRO, "Custom", 240, 3840), "240x3840")  # 1:16, 921,600 px
        self.assertEqual(resolve(V40, "Custom", 4096, 256), "4096x256")  # 16:1
        self.assertEqual(resolve(LITE, "Custom", 2560, 1440), "2560x1440")
        with self.assertRaisesRegex(Exception, "aspect ratio range"):
            resolve(PRO, "Custom", 200, 4608)  # 921,600 px but narrower than 1:16
        with self.assertRaisesRegex(Exception, "Minimum image resolution"):
            resolve(LITE, "Custom", 1920, 1080)  # Lite / 4.5 need 3.69 MP

    async def test_size_validation(self):
        resolve = nodes_seedream.resolve_seedream_size
        self.assertEqual(resolve(PRO, "Custom", 1024, 1024), "1024x1024")
        self.assertEqual(resolve(PRO, "1.5K (adaptive)", 0, 0), "1.5K")
        self.assertEqual(resolve(V40, "Custom", 1280, 1024), "1280x1024")
        self.assertEqual(resolve(V45, "(4K) 6240x2656 (21:9)", 0, 0), "6240x2656")
        with self.assertRaisesRegex(Exception, "Maximum image resolution .* is 4.62MP"):
            resolve(FLASH, "Custom", 2160, 2160)
        with self.assertRaisesRegex(Exception, "Minimum image resolution .* is 3.69MP"):
            resolve(LITE, "Custom", 1024, 1024)
        with self.assertRaisesRegex(Exception, "Maximum image resolution .* is 16.78MP"):
            resolve(V40, "Custom", 6240, 4992)
        with self.assertRaisesRegex(Exception, "Prompt cannot be empty"):
            await self.run_node(PRO, prompt="   ")

    async def test_reference_limits(self):
        import torch

        with self.assertRaisesRegex(Exception, "Maximum of 10 reference images .* 11 received"):
            await self.run_node(FLASH, **{"images": {"image_1": torch.zeros((11, 8, 8, 3))}})
        with self.assertRaisesRegex(Exception, "Maximum of 14 reference images .* 15 received"):
            await self.run_node(LITE, **{"images": {"image_1": torch.zeros((15, 8, 8, 3))}})
        fake, _c, _r = await self.run_node(LITE, **{"images": {"image_1": torch.zeros((14, 8, 8, 3))}})
        self.assertEqual(len(fake.calls[0]["image"]), 14)
        # references + max_images <= 15 when batching
        with self.assertRaisesRegex(Exception, "cannot exceed 15"):
            await self.run_node(
                V45, max_images=6, **{"images": {"image_1": torch.zeros((10, 8, 8, 3))}}
            )
        fake, _c, _r = await self.run_node(
            V45, max_images=5, **{"images": {"image_1": torch.zeros((10, 8, 8, 3))}}
        )
        self.assertEqual(fake.calls[0]["sequential_image_generation_options"].max_images, 5)
        # aspect ratio 1:16 .. 16:1
        with self.assertRaisesRegex(Exception, "between 1:16 and 16:1"):
            await self.run_node(PRO, **{"images": {"image_1": torch.zeros((1, 170, 10, 3))}})
        await self.run_node(PRO, **{"images": {"image_1": torch.zeros((1, 160, 10, 3))}})

    async def test_generation_count_runs_parallel_with_seed_offsets(self):
        fake, client, result = await self.run_node(
            PRO, seed=2147483647, generation_count=2
        )
        self.assertEqual(sorted(call["seed"] for call in fake.calls), [0, 2147483647])
        self.assertEqual(result[0].shape[0], 2)

    async def test_transparent_background(self):
        import torch

        mask = torch.zeros((1, 8, 8))
        mask[:, :, :4] = 1.0  # left half transparent (Load Image convention)
        fake, _c, result = await self.run_node(
            FLASH,
            background="transparent",
            output_format="png",
            reference_mask=mask,
            **{"images": {"image_1": torch.ones((1, 8, 8, 3))}},
        )
        request = fake.calls[0]
        self.assertEqual(request["extra_body"], {"background": "transparent"})
        self.assertEqual(request["output_format"], "png")
        header, png = decode_data_uri(request["image"])
        self.assertEqual(header, "data:image/png;base64")
        self.assertEqual(png.mode, "RGBA")
        self.assertEqual((png.getpixel((0, 0))[3], png.getpixel((7, 0))[3]), (0, 255))
        image, _response, output_mask = result
        self.assertEqual(image.shape[-1], 3)
        self.assertAlmostEqual(float(output_mask[0, 0, 0]), 0.75)

        with self.assertRaisesRegex(Exception, "exactly one reference image"):
            await self.run_node(PRO, background="transparent", output_format="png")
        with self.assertRaisesRegex(Exception, "set output_format to png"):
            await self.run_node(
                PRO, background="transparent", **{"images": {"image_1": torch.ones((1, 8, 8, 3))}}
            )

    async def test_fail_on_partial(self):
        events = [image_event(0), failed_event(1), completed_event(LITE, 1)]
        fake = FakeImages(stream_events=events)
        with self.assertRaisesRegex(Exception, "Only 1 of 2 images were generated"):
            await self.run_node(LITE, fake=fake, max_images=2, fail_on_partial=True)

        fake = FakeImages(stream_events=events)
        _fake, _client, result = await self.run_node(
            LITE, fake=fake, max_images=2, fail_on_partial=False
        )
        image, response, _mask = result
        self.assertEqual(image.shape[0], 1)
        failed = json.loads(response)[0]["failed_images"]
        self.assertEqual(failed[0]["index"], 2)
        self.assertEqual(failed[0]["code"], "OutputImageSensitiveContentDetected")

    async def test_fail_on_partial_counts_failed_generations(self):
        class FlakyImages(FakeImages):
            def generate(self, **kwargs):
                if kwargs["seed"] == 43:
                    raise RuntimeError("{'code': 'InternalServiceError', 'message': 'boom'}")
                return super().generate(**kwargs)

        with self.assertRaisesRegex(Exception, "Only 1 of 2 generations succeeded"):
            await self.run_node(
                V40, fake=FlakyImages(), generation_count=2, fail_on_partial=True
            )
        _fake, _client, result = await self.run_node(
            V40, fake=FlakyImages(), generation_count=2, fail_on_partial=False
        )
        self.assertEqual(result[0].shape[0], 1)


@requires_comfyui
class LayerSeparationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def layer_item(size, color, z_index, absolute, name="leaf", description="a leaf"):
        return SimpleNamespace(
            b64_json=png_b64(size, color),
            z_index=z_index,
            bounding_box=None if absolute is None else SimpleNamespace(
                absolute=absolute, normalized=None
            ),
            name=name,
            description=description,
            size=f"{size[0]}x{size[1]}",
        )

    def base_item(self, size=(8, 8)):
        return SimpleNamespace(
            b64_json=png_b64(size, (255, 0, 0, 255)), z_index=0, bounding_box=None,
            name=None, description=None, size=f"{size[0]}x{size[1]}",
        )

    async def run_node(self, data, model=PRO, image=None, **options):
        import torch

        calls = []

        from byteplussdkarkruntime.resources.images.images import Images as SdkImages

        def generate(**kwargs):
            assert_matches_sdk(SdkImages.generate, kwargs)
            calls.append(kwargs)
            return SimpleNamespace(model=kwargs["model"], data=data)

        client = FakeClient(SimpleNamespace(generate=generate))
        if image is None:
            image = torch.ones((1, 600, 600, 3))
        result = await nodes_seedream.BytePlusSeedreamLayerSeparation.execute(
            client, {"model": model, "image": image, "prompt": "  split  ", "size": "auto",
                     "seed": 42, "watermark": False, **options}
        )
        return calls, client, result

    async def test_crop_layers_omitted_default_and_explicit_overrides(self):
        leaf = self.layer_item((4, 4), (0, 255, 0, 128), 1, [2, 2, 6, 6])
        for model in (PRO, FLASH, V55):
            for supplied in (None, False, True):
                with self.subTest(model=model, crop_layers=supplied):
                    options = {} if supplied is None else {"crop_layers": supplied}
                    _, _, result = await self.run_node([self.base_item(), leaf], model=model, **options)
                    cropped = model == V55 if supplied is None else supplied
                    side = 4 if cropped else 8
                    self.assertEqual(tuple(result[2].shape), (1, side, side, 3))
                    self.assertEqual(tuple(result[3].shape), (1, side, side))
                    self.assertEqual(result[5]["canvas"], (8, 8))

    async def test_request_and_full_canvas_outputs(self):
        leaf = self.layer_item((4, 4), (0, 255, 0, 128), 1, [2, 2, 6, 6])
        calls, client, result = await self.run_node(
            [self.base_item(), leaf], prompt_optimization="standard"
        )
        request = calls[0]
        self.assertTrue(request["layer_decomposition"])
        self.assertEqual(request["model"], "dola-seedream-5-0-pro-260628")
        self.assertEqual(request["prompt"], "split")
        self.assertEqual(request["size"], "auto")
        self.assertEqual(request["seed"], 42)
        self.assertEqual(request["response_format"], "b64_json")
        self.assertEqual(request["output_format"], "png")
        self.assertEqual(request["optimize_prompt_options"].mode, "standard")
        header, png = decode_data_uri(request["image"])
        self.assertEqual((header, png.size), ("data:image/png;base64", (600, 600)))

        base_image, base_mask, layers, masks, bboxes, layer_stack, layers_json = result
        self.assertEqual(tuple(base_image.shape), (1, 8, 8, 3))
        self.assertAlmostEqual(float(base_image[0, 0, 0, 0]), 1.0)
        self.assertEqual(tuple(base_mask.shape), (1, 8, 8))
        self.assertEqual(float(base_mask.sum()), 0.0)
        self.assertEqual(tuple(layers.shape), (1, 8, 8, 3))
        self.assertEqual(tuple(masks.shape), (1, 8, 8))
        self.assertEqual(float(masks[0, 0, 0]), 1.0)  # outside the layer: transparent
        self.assertAlmostEqual(float(masks[0, 3, 3]), 1.0 - 128 / 255, places=3)
        self.assertAlmostEqual(float(layers[0, 3, 3, 1]), 1.0, places=3)
        self.assertEqual(float(layers[0, 0, 0].sum()), 0.0)  # black canvas

        self.assertEqual(len(bboxes), 1)
        box = bboxes[0][0]
        self.assertEqual((box["x"], box["y"], box["width"], box["height"]), (0, 0, 8, 8))
        self.assertEqual(
            box["metadata"],
            {"name": "leaf", "desc": "a leaf", "z_index": 1, "native_size": "4x4",
             "content_rect": [2, 2, 4, 4], "flags": []},
        )
        self.assertEqual(layer_stack["version"], 1)
        self.assertEqual(layer_stack["canvas"], (8, 8))
        background, element = layer_stack["layers"]
        self.assertEqual(
            {k: background[k] for k in ("type", "x", "y", "z_index", "name")},
            {"type": "raster", "x": 0, "y": 0, "z_index": 0, "name": "background"},
        )
        self.assertEqual((element["x"], element["y"], element["z_index"], element["name"]), (2, 2, 1, "leaf"))
        self.assertEqual(tuple(element["image"].shape), (1, 4, 4, 3))
        self.assertEqual(tuple(element["mask"].shape), (1, 4, 4))

        info = json.loads(layers_json)
        self.assertEqual(info["model"], "dola-seedream-5-0-pro-260628")
        self.assertEqual([item["z_index"] for item in info["layers"]], [0, 1])
        self.assertEqual(info["layers"][1]["bounding_box"]["absolute"], [2, 2, 6, 6])
        self.assertIsNone(info["layers"][1]["file"])

    async def test_55_layer_options_and_rich_text_are_preserved(self):
        from byteplussdkarkruntime.types.images.images import Image

        leaf = self.layer_item((4, 4), (0, 255, 0, 128), 1, [2, 2, 6, 6])
        ocr = {
            "text": "SALE",
            "lines": [{
                "bounding_box": {"absolute": [2, 2, 6, 6], "normalized": [250, 250, 750, 750]},
                "runs": [{"text": "SALE", "style": {
                    "similar_font_family": "Impact", "foreground_color": "#E53935",
                    "bold": True, "italic": False, "underline": True, "strikethrough": False,
                    "shadow": False, "translucent": False,
                }}],
            }],
        }
        leaf = Image(
            **{**vars(leaf), "url": "https://example.invalid/layer.png", "bounding_box": vars(leaf.bounding_box)},
            text_recognition=ocr, background="transparent",
        )
        base = self.base_item()
        base.background = "opaque"
        calls, _, result = await self.run_node(
            [base, leaf], model=V55, max_images=100, text_recognition=True,
            safety_identifier="hashed-user",
        )
        request = calls[0]
        self.assertEqual(request["model"], "dola-seedream-5-5-261028")
        self.assertEqual(request["optimize_prompt_options"].mode, "standard")
        self.assertEqual(request["extra_body"], {
            "layer_decomposition_options": {"max_images": 100, "text_recognition": True},
            "safety_identifier": "hashed-user",
        })
        self.assertNotIn("seed", request)
        self.assertNotIn("background", request)
        info = json.loads(result[-1])
        self.assertEqual(info["layers"][0]["background"], "opaque")
        self.assertEqual(info["layers"][1]["background"], "transparent")
        self.assertEqual(info["layers"][1]["text_recognition"], ocr)

    async def test_55_layer_defaults_boundaries_and_absent_ocr(self):
        leaf = self.layer_item((4, 4), (0, 255, 0, 255), 1, [0, 0, 4, 4])
        for count in (None, 2, 100):
            with self.subTest(count=count):
                calls, _, result = await self.run_node(
                    [self.base_item(), leaf], model=V55, **({} if count is None else {"max_images": count}),
                )
                self.assertEqual(calls[0]["extra_body"]["layer_decomposition_options"],
                                 {"max_images": 30 if count is None else count, "text_recognition": False})
                self.assertNotIn("text_recognition", json.loads(result[-1])["layers"][1])

    async def test_55_omitted_layer_options_accept_comfyui_none(self):
        leaf = self.layer_item((4, 4), (0, 255, 0, 255), 1, [0, 0, 4, 4])
        for options in (
            {"safety_identifier": None},
            {"max_images": None},
            {"text_recognition": None},
            {"prompt_optimization": None},
            {"safety_identifier": None, "max_images": None, "text_recognition": None},
        ):
            with self.subTest(options=options):
                calls, _, result = await self.run_node(
                    [self.base_item(), leaf], model=V55, **options,
                )
                self.assertEqual(calls[0]["extra_body"], {
                    "layer_decomposition_options": {"max_images": 30, "text_recognition": False},
                })
                self.assertEqual(calls[0]["optimize_prompt_options"].mode, "standard")
                self.assertEqual(result[2].shape[0], 1)

    async def test_55_layer_invalid_parameters_fail_before_submission(self):
        leaf = self.layer_item((4, 4), (0, 255, 0, 255), 1, [0, 0, 4, 4])
        cases = [
            ({"max_images": 1}, "max_images"),
            ({"max_images": 101}, "max_images"),
            ({"max_images": 2.5}, "max_images"),
            ({"max_images": True}, "max_images"),
            ({"background": "auto"}, "background"),
            ({"prompt_optimization": "fast"}, "prompt optimization"),
            ({"size": "2048x2048"}, "size"),
            ({"output_format": "webp"}, "output format"),
            ({"safety_identifier": "a" * 65}, "safety_identifier"),
            ({"text_recognition": "false"}, "text_recognition"),
        ]
        for options, message in cases:
            with self.subTest(options=options):
                with mock.patch.object(nodes_seedream, "call_billed") as paid_call:
                    with self.assertRaisesRegex(Exception, message):
                        await self.run_node([self.base_item(), leaf], model=V55, **options)
                    paid_call.assert_not_called()

    async def test_55_layer_partial_failure_is_not_silently_dropped(self):
        leaf = self.layer_item((4, 4), (0, 255, 0, 255), 1, [0, 0, 4, 4])
        for failed in (None, SimpleNamespace(b64_json=None, error=SimpleNamespace(code="LayerFailed"))):
            with self.subTest(failed=failed):
                with self.assertRaisesRegex(Exception, "incomplete layer"):
                    await self.run_node([self.base_item(), leaf, failed], model=V55)

    async def test_55_layer_count_can_return_99_layers(self):
        layers = [
            self.layer_item((2, 2), (0, 255, 0, 128), index, [0, 0, 2, 2])
            for index in range(1, 100)
        ]
        _, _, result = await self.run_node([self.base_item(), *layers], model=V55, max_images=100)
        self.assertEqual(result[2].shape[0], 99)
        self.assertEqual(len(json.loads(result[-1])["layers"]), 100)

    async def test_55_sdk_serializes_layer_options_without_background(self):
        import httpx
        import torch
        from byteplussdkarkruntime import Ark

        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "model": "dola-seedream-5-5-261028",
                "created": 1,
                "data": [
                    {"b64_json": png_b64((8, 8), (255, 255, 255, 255)), "z_index": 0},
                    {"b64_json": png_b64((8, 8), (255, 0, 0, 128)), "z_index": 1},
                ],
            })

        with Ark(
            api_key="test-only",
            base_url="https://example.invalid/api/v3",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        ) as ark:
            await nodes_seedream.BytePlusSeedreamLayerSeparation.execute(
                FakeClient(ark.images),
                {"model": V55, "image": torch.ones((1, 512, 512, 3)), "max_images": 10,
                 "text_recognition": True, "safety_identifier": "hashed-user"},
            )
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["layer_decomposition_options"], {"max_images": 10, "text_recognition": True})
        self.assertEqual(requests[0]["safety_identifier"], "hashed-user")
        self.assertNotIn("background", requests[0])
        self.assertEqual(requests[0]["response_format"], "b64_json")

    async def test_pro_endpoint_also_applies_to_layers(self):
        leaf = self.layer_item((4, 4), (0, 255, 0, 255), 1, [0, 0, 4, 4])
        with mock.patch.dict(os.environ, {"BYTEPLUS_SEEDREAM_5_0_PRO_ENDPOINT": "ep-layers"}):
            calls, _, _ = await self.run_node([self.base_item(), leaf])
        self.assertEqual(calls[0]["model"], "ep-layers")
        self.assertTrue(calls[0]["layer_decomposition"])

    async def test_flash_has_no_prompt_optimization(self):
        leaf = self.layer_item((4, 4), (0, 255, 0, 255), 1, [0, 0, 4, 4])
        calls, _client, _result = await self.run_node([self.base_item(), leaf], model=FLASH)
        self.assertEqual(calls[0]["model"], "dola-seedream-5-0-flash-260915")
        self.assertNotIn("optimize_prompt_options", calls[0])

    async def test_layers_are_resized_to_bbox_and_sorted(self):
        small = self.layer_item((2, 2), (0, 0, 255, 255), 2, [0, 0, 4, 4], name="top")
        leaf = self.layer_item((4, 4), (0, 255, 0, 255), 1, [4, 4, 8, 8], name="bottom")
        # Base listed last: it is found by z_index 0.
        _calls, _client, result = await self.run_node([small, leaf, self.base_item()])
        _base, _bm, layers, masks, bboxes, layer_stack, _json = result
        names = [box["metadata"]["name"] for box in bboxes[0]]
        self.assertEqual(names, ["bottom", "top"])
        self.assertEqual(bboxes[0][1]["metadata"]["flags"], ["resized_to_bbox"])
        self.assertEqual(bboxes[0][1]["metadata"]["native_size"], "2x2")
        self.assertAlmostEqual(float(layers[1, 1, 1, 2]), 1.0, places=3)
        self.assertAlmostEqual(float(masks[1, 1, 1]), 0.0, places=3)
        self.assertEqual(float(masks[1, 6, 6]), 1.0)
        self.assertEqual([item["z_index"] for item in layer_stack["layers"]], [0, 1, 2])

    async def test_crop_layers_minimal_size(self):
        a = self.layer_item((4, 4), (0, 255, 0, 255), 1, [1, 1, 5, 5], name="a")
        b = self.layer_item((2, 3), (0, 0, 255, 255), 2, [5, 4, 7, 7], name="b")
        _calls, _client, result = await self.run_node([self.base_item(), a, b], crop_layers=True)
        _base, _bm, layers, masks, bboxes, layer_stack, _json = result
        self.assertEqual(tuple(layers.shape), (2, 4, 4, 3))
        self.assertEqual(tuple(masks.shape), (2, 4, 4))
        self.assertEqual(float(masks[1, 2, 1]), 0.0)  # inside b's 2x3 content
        self.assertEqual(float(masks[1, 3, 3]), 1.0)  # padding
        box = bboxes[0][1]
        self.assertEqual((box["x"], box["y"], box["width"], box["height"]), (5, 4, 4, 4))
        self.assertEqual(box["metadata"]["content_rect"], [0, 0, 2, 3])
        self.assertEqual(layer_stack["canvas"], (8, 8))
        self.assertEqual((layer_stack["layers"][2]["x"], layer_stack["layers"][2]["y"]), (5, 4))

    async def test_missing_degenerate_and_clamped_boxes(self):
        missing = self.layer_item((8, 8), (0, 255, 0, 255), 1, None, name="missing")
        degenerate = self.layer_item((4, 4), (0, 255, 0, 255), 2, [3, 3, 3, 6], name="flat")
        clamped = self.layer_item((8, 8), (0, 255, 0, 255), 3, [-2, 0, 10, 8], name="wide")
        _calls, _client, result = await self.run_node(
            [self.base_item(), missing, degenerate, clamped]
        )
        _base, _bm, layers, masks, bboxes, layer_stack, _json = result
        flags = [box["metadata"]["flags"] for box in bboxes[0]]
        self.assertEqual(flags[0], ["bbox_missing"])
        self.assertEqual(flags[1], ["bbox_degenerate"])
        self.assertIn("bbox_clamped", flags[2])
        self.assertEqual(float(masks[0].sum()), 0.0)  # missing box: full canvas, opaque
        self.assertEqual(float(masks[1].min()), 1.0)  # degenerate: left empty
        self.assertEqual(len(layer_stack["layers"]), 3)  # degenerate layer has no stack item

    async def test_response_and_input_validation(self):
        import torch

        leaf = self.layer_item((4, 4), (0, 255, 0, 255), 1, [0, 0, 4, 4])
        with self.assertRaisesRegex(Exception, "no base image"):
            await self.run_node([])
        with self.assertRaisesRegex(Exception, "first item is not the base image"):
            await self.run_node([leaf])
        with self.assertRaisesRegex(Exception, "returned no layers"):
            await self.run_node([self.base_item()])
        with self.assertRaisesRegex(Exception, "Only a single input image"):
            await self.run_node([self.base_item(), leaf], image=torch.ones((2, 600, 600, 3)))
        with self.assertRaisesRegex(Exception, "at least 262,144 pixels"):
            await self.run_node([self.base_item(), leaf], image=torch.ones((1, 600, 400, 3)))
        with self.assertRaisesRegex(Exception, "between 1:16 and 16:1"):
            await self.run_node([self.base_item(), leaf], image=torch.ones((1, 2000, 100, 3)))

    async def test_large_input_is_downscaled_to_about_4mp(self):
        import torch

        leaf = self.layer_item((4, 4), (0, 255, 0, 255), 1, [0, 0, 4, 4])
        calls, _client, _result = await self.run_node(
            [self.base_item(), leaf], image=torch.ones((1, 1600, 3000, 3))
        )
        _header, png = decode_data_uri(calls[0]["image"])
        width, height = png.size
        self.assertLessEqual(width * height, 2048 * 2048)
        self.assertEqual((width % 2, height % 2), (0, 0))
        self.assertAlmostEqual(width / height, 3000 / 1600, places=2)

    async def test_save_layers_writes_files(self):
        leaf = self.layer_item((4, 4), (0, 255, 0, 128), 1, [2, 2, 6, 6])
        old_output = seedream_utils.folder_paths.get_output_directory
        with tempfile.TemporaryDirectory() as tmp:
            seedream_utils.folder_paths.get_output_directory = lambda: tmp
            try:
                _calls, _client, result = await self.run_node(
                    [self.base_item(), leaf], save_layers=True, filename_prefix="BytePlus/Test/Layers"
                )
            finally:
                seedream_utils.folder_paths.get_output_directory = old_output
            info = json.loads(result[6])["layers"]
            for item in info:
                self.assertTrue(os.path.exists(os.path.join(tmp, item["file"])))
            self.assertTrue(info[0]["file"].endswith("_base.png"))
            self.assertTrue(info[1]["file"].endswith("_layer01.png"))

    async def test_saved_layers_without_z_index_do_not_overwrite_each_other(self):
        first = self.layer_item((4, 4), (0, 255, 0, 255), None, [0, 0, 4, 4], name="first")
        second = self.layer_item((4, 4), (0, 0, 255, 255), None, [4, 4, 8, 8], name="second")
        old_output = seedream_utils.folder_paths.get_output_directory
        with tempfile.TemporaryDirectory() as tmp:
            seedream_utils.folder_paths.get_output_directory = lambda: tmp
            try:
                _calls, _client, result = await self.run_node(
                    [self.base_item(), first, second], save_layers=True, filename_prefix="BytePlus/Test/Layers"
                )
            finally:
                seedream_utils.folder_paths.get_output_directory = old_output
            files = [item["file"] for item in json.loads(result[6])["layers"]]
            self.assertEqual(len(set(files)), 3)
            self.assertTrue(files[0].endswith("_base.png"))
            for relative in files:
                self.assertTrue(os.path.exists(os.path.join(tmp, relative)))

    async def test_api_error_in_response_is_raised(self):
        error = SimpleNamespace(code="OutputImageSensitiveContentDetected", message="output image may contain sensitive information")

        def generate(**_kwargs):
            return SimpleNamespace(model="m", data=[], error=error)

        client = FakeClient(SimpleNamespace(generate=generate))
        import torch

        with self.assertRaisesRegex(Exception, "Generated image contains sensitive content"):
            await nodes_seedream.BytePlusSeedreamLayerSeparation.execute(
                client, {"model": PRO, "image": torch.ones((1, 600, 600, 3))}
            )


if __name__ == "__main__":
    unittest.main()
