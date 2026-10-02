import base64
import importlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace


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
    nodes_image = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_image")
    nodes_seedream = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_seedream")


PRO = "seedream 5.0 pro"
FLASH = "seedream 5.0 flash"
LITE = "seedream 5.0 lite"
V45 = "seedream-4-5-251128"
V40 = "seedream-4-0-250828"

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
    """BytePlusClients stand-in: records quota calls, exposes a fake Ark client."""

    def __init__(self, images):
        self.ark = SimpleNamespace(images=images)
        self.quota = []
        self.usage = []

    def check_quota(self, model, cost):
        self.quota.append((model, cost))

    def update_usage(self, model, cost):
        self.usage.append((model, cost))


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


# Inputs whose limits follow the BytePlus docs instead of core: 14 reference
# images on 4.5 / 4.0, max_images up to 15, custom width/height bounds from the
# pixel range and aspect limit (core: min 1024), and the layer-separation input
# (262,144 total pixels) and <bbox> range (0-999).
BYTEPLUS_LIMIT_DEVIATIONS = {
    ("BytePlusSeedream", LITE, "max_images"),
    ("BytePlusSeedream", V45, "max_images"),
    ("BytePlusSeedream", V45, "images"),
    ("BytePlusSeedream", V40, "max_images"),
    ("BytePlusSeedream", V40, "images"),
    *(("BytePlusSeedream", option, side) for option in (PRO, FLASH, LITE, V45, V40) for side in ("width", "height")),
    ("BytePlusSeedreamLayerSeparation", PRO, "image"),
    ("BytePlusSeedreamLayerSeparation", PRO, "prompt"),
    ("BytePlusSeedreamLayerSeparation", FLASH, "image"),
    ("BytePlusSeedreamLayerSeparation", FLASH, "prompt"),
}


@requires_comfyui
class SeedreamSchemaTests(unittest.TestCase):
    def test_top_level_order_and_outputs(self):
        schema = nodes_seedream.BytePlusSeedream.define_schema()
        self.assertEqual(schema.node_id, "BytePlusSeedream")
        self.assertEqual(schema.display_name, "BytePlus Seedream 4.5 & 5.0")
        self.assertEqual([item.id for item in schema.inputs], ["client", "prompt", "model"])
        self.assertEqual(schema.inputs[0].io_type, "BYTEPLUS_CLIENT")
        self.assertEqual(schema.inputs[2].io_type, "COMFY_DYNAMICCOMBO_V3")
        self.assertEqual(
            [option.key for option in schema.inputs[2].options], [PRO, FLASH, LITE, V45, V40]
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
                for name in ("generation_count", "output_format", "background"):
                    if name in inputs:
                        self.assertTrue(inputs[name].advanced, name)
                for name in ("fail_on_partial", "prompt_optimization", "watermark", "thinking"):
                    if name in inputs:
                        self.assertTrue(inputs[name].advanced, name)
                for name in ("size_preset", "width", "height", "seed"):
                    self.assertFalse(inputs[name].advanced, name)
                seed = inputs["seed"]
                self.assertEqual((seed.default, seed.min, seed.max), (42, 0, 2147483647))
                self.assertTrue(seed.control_after_generate)
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
            },
        )
        self.assertEqual(
            models_config.SEEDREAM_LAYER_SEPARATION_MODELS,
            {PRO: "dola-seedream-5-0-pro-260628", FLASH: "dola-seedream-5-0-flash-260915"},
        )

    def test_layer_separation_schema(self):
        schema = nodes_seedream.BytePlusSeedreamLayerSeparation.define_schema()
        self.assertEqual(schema.node_id, "BytePlusSeedreamLayerSeparation")
        self.assertEqual(schema.display_name, "BytePlus Seedream 5.0 Layer Separation")
        self.assertEqual([item.id for item in schema.inputs], ["client", "model"])
        for model, expected in LAYER_OPTION_INPUTS.items():
            with self.subTest(model=model):
                inputs, ids = option_inputs(schema, model)
                self.assertEqual(ids, expected)
                self.assertEqual(inputs["size"].options, ["auto", "1K", "1.5K", "2K"])
                self.assertEqual(inputs["seed"].default, 42)
                self.assertTrue(inputs["seed"].control_after_generate)
                self.assertFalse(inputs["crop_layers"].advanced)
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

    def test_legacy_nodes_are_deprecated(self):
        for node, name in (
            (nodes_image.BytePlusSeedream4, "BytePlus Seedream 4 (Legacy)"),
            (nodes_image.BytePlusSeedream5, "BytePlus Seedream 5 (Legacy)"),
            (nodes_image.BytePlusSeedreamLayers, "BytePlus Seedream Layer Decomposition (Legacy)"),
        ):
            schema = node.define_schema()
            self.assertTrue(schema.is_deprecated)
            self.assertEqual(schema.display_name, name)
        self.assertEqual(
            nodes_seedream.NODES,
            [nodes_seedream.BytePlusSeedream, nodes_seedream.BytePlusSeedreamLayerSeparation],
        )

    def test_documented_byteplus_limits(self):
        """Where BytePlus documents other limits than core uses, ours follow BytePlus."""
        schema = nodes_seedream.BytePlusSeedream.define_schema()
        options = {o.key: o for o in schema.inputs[2].options}
        for key, refs in ((LITE, 14), (V45, 14), (V40, 14), (PRO, 10), (FLASH, 10)):
            inputs = {i.id: i for i in options[key].inputs}
            self.assertEqual(len(inputs["images"].template.names), refs, key)
            if "max_images" in inputs:
                self.assertEqual(inputs["max_images"].max, 15, key)
        layers = nodes_seedream.BytePlusSeedreamLayerSeparation.define_schema()
        for option in layers.inputs[1].options:
            inputs = {i.id: i for i in option.inputs}
            self.assertIn("262,144 pixels", inputs["image"].tooltip)
            self.assertIn("0-999", inputs["prompt"].tooltip)

    def test_matches_core_schema(self):
        """Every core input (id, type, default, limits, tooltip, advanced) is copied as is."""
        try:
            core = importlib.import_module("comfy_api_nodes.nodes_bytedance")
            pairs = [
                (core.ByteDanceSeedreamNodeV3, nodes_seedream.BytePlusSeedream, EXTRAS),
                (
                    core.ByteDanceSeedreamLayerSeparationNodeV2,
                    nodes_seedream.BytePlusSeedreamLayerSeparation,
                    {"output_format", "save_layers", "filename_prefix"},
                ),
            ]
        except Exception as exc:  # core API nodes unavailable in this checkout
            self.skipTest(f"comfy_api_nodes.nodes_bytedance not importable: {exc}")

        def describe(item):
            data = item.as_dict()
            data.pop("options", None)
            data.pop("template", None)
            return item.id, item.io_type, data

        for core_node, our_node, extras in pairs:
            core_schema = core_node.define_schema()
            our_schema = our_node.define_schema()
            self.assertEqual(core_schema.description, our_schema.description)
            our_top = our_schema.inputs[1:]  # client first
            self.assertEqual([i.id for i in core_schema.inputs], [i.id for i in our_top])
            for core_input, our_input in zip(core_schema.inputs, our_top):
                if core_input.io_type != "COMFY_DYNAMICCOMBO_V3":
                    self.assertEqual(describe(core_input), describe(our_input))
                    continue
                self.assertEqual(
                    [o.key for o in core_input.options], [o.key for o in our_input.options]
                )
                for core_option, our_option in zip(core_input.options, our_input.options):
                    ours = [i for i in our_option.inputs if i.id not in extras]
                    self.assertEqual(
                        [i.id for i in core_option.inputs], [i.id for i in ours], core_option.key
                    )
                    for core_item, our_item in zip(core_option.inputs, ours):
                        if (our_schema.node_id, core_option.key, core_item.id) in BYTEPLUS_LIMIT_DEVIATIONS:
                            continue  # checked in test_documented_byteplus_limits
                        with self.subTest(node=our_schema.node_id, option=core_option.key, input=core_item.id):
                            self.assertEqual(describe(core_item), describe(our_item))
                            if core_item.io_type == "COMBO":
                                core_options = list(core_item.options)
                                self.assertEqual(
                                    [o for o in our_item.options if o in core_options], core_options
                                )
                            if core_item.io_type == "COMFY_AUTOGROW_V3":
                                self.assertEqual(
                                    core_item.template.as_dict(), our_item.template.as_dict()
                                )
            for core_output, our_output in zip(core_schema.outputs, our_schema.outputs):
                self.assertEqual(
                    (core_output.io_type, core_output.display_name, core_output.tooltip),
                    (our_output.io_type, our_output.display_name, our_output.tooltip),
                )


@requires_comfyui
class SeedreamRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import torch

        self._old = (
            nodes_image.download_url_to_image_tensor_async,
            nodes_image.download_url_to_rgba_tensor_async,
            getattr(executor.PromptServer, "instance", None),
            getattr(nodes_seedream.BytePlusSeedream, "hidden", None),
        )

        async def fake_rgb(_session, _url):
            return torch.zeros((1, 4, 4, 3))

        async def fake_rgba(_session, _url):
            tensor = torch.ones((1, 4, 4, 4))
            tensor[..., 3] = 0.25
            return tensor

        nodes_image.download_url_to_image_tensor_async = fake_rgb
        nodes_image.download_url_to_rgba_tensor_async = fake_rgba
        executor.PromptServer.instance = SimpleNamespace()
        nodes_seedream.BytePlusSeedream.hidden = SimpleNamespace(unique_id="test-node", prompt={})

    def tearDown(self):
        nodes_image.download_url_to_image_tensor_async = self._old[0]
        nodes_image.download_url_to_rgba_tensor_async = self._old[1]
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
        self.assertEqual(client.quota, [("dola-seedream-5-0-pro-260628", 1)])
        image, response, mask = result
        self.assertEqual(tuple(image.shape), (1, 4, 4, 3))
        self.assertEqual(float(mask.sum()), 0.0)
        self.assertEqual(json.loads(response)[0]["urls"], ["https://example.invalid/image0.png"])

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
        self.assertEqual(client.quota, [("seedream-5-0-260128", 3)])
        self.assertEqual(client.usage, [("seedream-5-0-260128", 3)])
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
        self.assertEqual((fake.calls, client.quota), ([], []))

        # Other models in eu-west-1, and Lite in ap-southeast-1, run.
        for model in (PRO, FLASH, V45, V40):
            with self.subTest(model=model):
                await nodes_seedream.BytePlusSeedream.execute(client, "a red apple", {"model": model})
        self.assertEqual(len(fake.calls), 4)
        client.region = "ap-southeast-1"
        await nodes_seedream.BytePlusSeedream.execute(client, "a red apple", {"model": LITE})
        self.assertEqual(fake.calls[-1]["model"], "seedream-5-0-260128")

    async def test_legacy_seedream5_lite_is_not_offered_in_eu_west_1(self):
        fake = FakeImages()
        client = FakeClient(fake)
        client.region = "eu-west-1"
        legacy = nodes_image.BytePlusSeedream5
        old_hidden = legacy.__dict__.get("hidden")
        legacy.hidden = SimpleNamespace(unique_id="legacy-node", prompt={})
        try:
            with self.assertRaises(Exception) as ctx:
                await legacy.execute(
                    client,
                    {"model_version": "seedream-5-0-lite", "prompt": "a red apple", "size": "2K (adaptive)"},
                )
        finally:
            if old_hidden is None:
                delattr(legacy, "hidden")
            else:
                legacy.hidden = old_hidden
        self.assertIn("seedream-5-0-260128 is not available in eu-west-1", str(ctx.exception))
        self.assertEqual((fake.calls, client.quota), ([], []))

    async def test_legacy_seedream4_stream_response_is_json(self):
        # The streamed completed event carries the SDK's Usage model; the legacy
        # node json.dumps its response, which used to raise TypeError.
        fake = FakeImages()
        legacy = nodes_image.BytePlusSeedream4
        old_hidden = getattr(legacy, "hidden", None)
        legacy.hidden = SimpleNamespace(unique_id="legacy-node", prompt={})
        try:
            image, response = await legacy.execute(
                FakeClient(fake), "seedream-4-5", "a red apple", False, 1,
                "2K (adaptive)", 2048, 2048, 1, 1, False,
            )
        finally:
            if old_hidden is None:
                delattr(legacy, "hidden")
            else:
                legacy.hidden = old_hidden
        self.assertTrue(fake.calls[0]["stream"])
        self.assertEqual(json.loads(response)[0]["usage"]["generated_images"], 1)

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
        self.assertEqual(client.quota, [("dola-seedream-5-0-pro-260628", 2)])
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
                     "seed": 42, "watermark": False, "crop_layers": False, **options}
        )
        return calls, client, result

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
        self.assertEqual(client.quota, [("dola-seedream-5-0-pro-260628", 1)])

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
        old_output = nodes_image.folder_paths.get_output_directory
        with tempfile.TemporaryDirectory() as tmp:
            nodes_image.folder_paths.get_output_directory = lambda: tmp
            try:
                _calls, _client, result = await self.run_node(
                    [self.base_item(), leaf], save_layers=True, filename_prefix="BytePlus/Test/Layers"
                )
            finally:
                nodes_image.folder_paths.get_output_directory = old_output
            info = json.loads(result[6])["layers"]
            for item in info:
                self.assertTrue(os.path.exists(os.path.join(tmp, item["file"])))
            self.assertTrue(info[0]["file"].endswith("_base.png"))
            self.assertTrue(info[1]["file"].endswith("_layer01.png"))

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
