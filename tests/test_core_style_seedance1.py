"""
Seedance 1.x nodes shaped like ComfyUI core's ByteDance nodes (nodes/nodes_seedance1.py).

Needs a ComfyUI checkout and a Python env with torch and the BytePlus SDK:
  COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_core_style_seedance1
The Ark client is faked; nothing here calls the real API.
"""
import asyncio
import importlib
import json
import os
import sys
import types
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock


COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
requires_comfyui = unittest.skipUnless(
    COMFY_ROOT, "Set COMFYUI_ROOT to a ComfyUI checkout to run these tests."
)

if COMFY_ROOT:
    if COMFY_ROOT not in sys.path:
        sys.path.insert(0, COMFY_ROOT)

    # ComfyUI owns the top-level ``utils`` package; preload it from ComfyUI.
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

    import torch  # noqa: E402

    models_config = importlib.import_module(f"{PACKAGE_NAME}.nodes.models_config")
    executor = importlib.import_module(f"{PACKAGE_NAME}.nodes.executor")
    nodes_video = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_video")
    seedance1 = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_seedance1")
    seedance2 = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_seedance2")
    from comfy_api.latest import io as comfy_io  # noqa: E402
    from comfy_execution.graph_utils import ExecutionBlocker  # noqa: E402

    T2V = seedance1.BytePlusSeedanceTextToVideo
    I2V = seedance1.BytePlusSeedanceImageToVideo
    FLF = seedance1.BytePlusSeedanceFirstLastFrame


def setUpModule():
    # Hide the tester's own BYTEPLUS_* variables and user/.env (see tests/support.py).
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


def assert_matches_sdk(method, kwargs):
    """Bind request kwargs to the real SDK signature (fakes accept anything)."""
    import inspect

    fn = inspect.unwrap(method)
    for cell in fn.__closure__ or ():
        if inspect.isfunction(cell.cell_contents) and cell.cell_contents.__name__ == fn.__name__:
            fn = cell.cell_contents
    inspect.signature(fn).bind(None, **kwargs)


def _image(width, height):
    return torch.zeros((1, height, width, 3), dtype=torch.float32)


CORE_INPUTS = [
    "model", "prompt", "resolution", "aspect_ratio", "duration",
    "seed", "camera_fixed", "watermark",
]
EXTRA_INPUTS = ["enable_offline_inference", "generation_count", "non_blocking"]
CORE_ADVANCED = {"camera_fixed", "watermark"}
CORE_OPTIONAL = {"seed", "camera_fixed", "watermark"}

# Core also offers Seedance 1.5 Pro, which BytePlus deprecated (shut down on
# 2026-11-11): these nodes leave it out, with its generate_audio input.
DEPRECATED_MODEL = "seedance-1-5-pro-251215"
# Documented deviations from core's inputs, checked in
# test_documented_byteplus_deviations instead of test_matches_core_nodes:
#   model          - core's options without DEPRECATED_MODEL (FLF defaults to 1.0 Pro)
#   duration       - BytePlus allows 2-12 s for 1.0 Pro / Pro Fast; core's widget starts at 3
#   generate_audio - Seedance 1.5 Pro only, left out with it
BYTEPLUS_DEVIATIONS = {"model", "duration", "generate_audio"}
REMOVED_CORE_INPUTS = {"generate_audio"}


def frontend_input_order(info):
    """
    Input names as the node's workflow JSON lists them: the optional client
    socket first, then required inputs, then the other optional ones.
    """
    optional = [name for name in info["input_order"]["optional"] if name != "client"]
    return ["client"] + info["input_order"]["required"] + optional


def _with_frames(frames):
    return CORE_INPUTS[:2] + frames + CORE_INPUTS[2:]


@requires_comfyui
class Seedance1SchemaTests(unittest.TestCase):
    EXPECTED_INPUTS = {
        "BytePlusSeedanceTextToVideo": ["client"] + _with_frames([]) + EXTRA_INPUTS,
        "BytePlusSeedanceImageToVideo": ["client"] + _with_frames(["image"]) + EXTRA_INPUTS,
        "BytePlusSeedanceFirstLastFrame": ["client"] + _with_frames(["first_frame", "last_frame"])
        + EXTRA_INPUTS,
    }

    def test_nodes_are_registered_in_order(self):
        self.assertEqual(seedance1.NODES, [T2V, I2V, FLF])
        self.assertEqual(
            [node.define_schema().display_name for node in seedance1.NODES],
            [
                "BytePlus Seedance Text to Video",
                "BytePlus Seedance Image to Video",
                "BytePlus Seedance First-Last-Frame to Video",
            ],
        )

    def test_input_order_core_first_client_first_extras_last(self):
        for node in seedance1.NODES:
            info = node.GET_NODE_INFO_V1()
            order = frontend_input_order(info)
            with self.subTest(node=info["name"]):
                self.assertEqual(order, self.EXPECTED_INPUTS[info["name"]])
                self.assertEqual(
                    [item.id for item in node.define_schema().inputs],
                    self.EXPECTED_INPUTS[info["name"]],
                )

    def test_model_options_labels_and_ids(self):
        full = ["seedance-1-0-pro-250528", "seedance-1-0-pro-fast-251015"]
        expected = {
            T2V: (full, "seedance-1-0-pro-fast-251015"),
            I2V: (full, "seedance-1-0-pro-fast-251015"),
            # 1.0 Pro Fast has no last-frame support.
            FLF: (full[:1], "seedance-1-0-pro-250528"),
        }
        for node, (options, default) in expected.items():
            model = node.define_schema().inputs[1]
            with self.subTest(node=node.__name__):
                self.assertEqual(model.id, "model")
                self.assertEqual(model.options, options)
                self.assertEqual(model.default, default)
                self.assertNotIn(DEPRECATED_MODEL, model.options)
        # Labels are core's; the values sent come from the BytePlus model map.
        self.assertEqual(
            models_config.SEEDANCE_1_MODELS,
            {
                "seedance-1-0-pro-250528": models_config.VIDEO_MODEL_MAP["seedance-1-0-pro"],
                "seedance-1-0-pro-fast-251015": models_config.VIDEO_MODEL_MAP["seedance-1-0-pro-fast"],
            },
        )

    def test_core_widget_definitions(self):
        for node in seedance1.NODES:
            inputs = {item.id: item for item in node.define_schema().inputs}
            with self.subTest(node=node.__name__):
                self.assertEqual(inputs["resolution"].options, ["480p", "720p", "1080p"])
                self.assertIsNone(inputs["resolution"].default)
                duration = inputs["duration"]
                # BytePlus: 2-12 s (core's widget starts at 3).
                self.assertEqual(
                    (duration.default, duration.min, duration.max, duration.step),
                    (5, 2, 12, 1),
                )
                self.assertEqual(duration.display_mode, comfy_io.NumberDisplay.slider)
                seed = inputs["seed"]
                self.assertEqual((seed.default, seed.min, seed.max), (0, 0, 2147483647))
                self.assertTrue(seed.control_after_generate)
                self.assertEqual(seed.tooltip, "Seed to use for generation.")
                self.assertTrue(inputs["prompt"].multiline)
                for name in ("camera_fixed", "watermark"):
                    self.assertIs(inputs[name].default, False)
                # Seedance 1.5 Pro only (deprecated): not offered.
                for name in ("generate_audio", "auto_duration", "draft_mode"):
                    self.assertNotIn(name, inputs)
                for name in CORE_INPUTS:
                    self.assertEqual(bool(inputs[name].optional), name in CORE_OPTIONAL, name)
                    self.assertEqual(bool(inputs[name].advanced), name in CORE_ADVANCED, name)
                for name in EXTRA_INPUTS:
                    self.assertTrue(inputs[name].advanced, name)
                    self.assertTrue(inputs[name].optional, name)
                # Optional: without a connected client the node uses the default key.
                self.assertTrue(inputs["client"].optional)

        ratios = ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9"]
        self.assertEqual(T2V.define_schema().inputs[4].options, ratios)
        for node in (I2V, FLF):
            aspect = next(i for i in node.define_schema().inputs if i.id == "aspect_ratio")
            self.assertEqual(aspect.options, ["adaptive"] + ratios)

    def test_outputs(self):
        for node in seedance1.NODES:
            info = node.GET_NODE_INFO_V1()
            with self.subTest(node=info["name"]):
                self.assertEqual(info["output"], ["VIDEO", "IMAGE", "STRING"])
                self.assertEqual(info["output_name"], ["VIDEO", "last_frame", "response"])
                # Every video of a generation_count batch; their last frames are one IMAGE batch.
                self.assertEqual(info["output_is_list"], [True, False, False])

    def _core_pairs(self):
        try:
            core = importlib.import_module("comfy_api_nodes.nodes_bytedance")
        except Exception as e:  # e.g. an older ComfyUI or API nodes unavailable
            self.skipTest(f"comfy_api_nodes.nodes_bytedance unavailable: {e}")
        return [
            (core.ByteDanceTextToVideoNode, T2V),
            (core.ByteDanceImageToVideoNode, I2V),
            (core.ByteDanceFirstLastFrameNode, FLF),
        ]

    def test_matches_core_nodes(self):
        """Core's inputs, compared as /object_info would show them."""
        for core_node, node in self._core_pairs():
            core_info, info = core_node.GET_NODE_INFO_V1(), node.GET_NODE_INFO_V1()
            core_order = [
                name
                for name in core_info["input_order"]["required"] + core_info["input_order"]["optional"]
                if name not in REMOVED_CORE_INPUTS
            ]
            order = frontend_input_order(info)
            with self.subTest(node=info["name"]):
                self.assertEqual(order[1 : 1 + len(core_order)], core_order)
                for section in ("required", "optional"):
                    for name, spec in core_info["input"][section].items():
                        if name in BYTEPLUS_DEVIATIONS:
                            continue  # checked in test_documented_byteplus_deviations
                        self.assertEqual(info["input"][section][name], spec, name)
                self.assertEqual(info["output"][:1], core_info["output"])
                self.assertEqual(info["output_name"][:1], core_info["output_name"])
                self.assertEqual(
                    info["display_name"],
                    core_info["display_name"].replace("ByteDance", "BytePlus Seedance"),
                )
                # Not an output node, like core's: an unconnected node does not run (and is not billed).
                self.assertFalse(info["output_node"])
                self.assertEqual(info["output_node"], core_info["output_node"])

    def test_documented_byteplus_deviations(self):
        """Where these nodes differ from core's (BYTEPLUS_DEVIATIONS), and nowhere else."""
        for core_node, node in self._core_pairs():
            core_info, info = core_node.GET_NODE_INFO_V1(), node.GET_NODE_INFO_V1()
            core_inputs = {**core_info["input"]["required"], **core_info["input"]["optional"]}
            inputs = {**info["input"]["required"], **info["input"]["optional"]}
            with self.subTest(node=info["name"]):
                # model: core's options in core's order, minus Seedance 1.5 Pro.
                core_model, model = core_inputs["model"][1], inputs["model"][1]
                self.assertIn(DEPRECATED_MODEL, core_model["options"])
                self.assertEqual(
                    model["options"], [o for o in core_model["options"] if o != DEPRECATED_MODEL]
                )
                expected_default = core_model["default"]
                if expected_default == DEPRECATED_MODEL:  # core's First-Last-Frame default
                    expected_default = "seedance-1-0-pro-250528"
                self.assertEqual(model["default"], expected_default)
                strip = lambda spec: {k: v for k, v in spec.items() if k not in ("options", "default")}
                self.assertEqual(strip(model), strip(core_model))
                # duration: BytePlus's 2 s minimum, everything else as core.
                self.assertEqual(core_inputs["duration"][1]["min"], 3)
                self.assertEqual(
                    inputs["duration"], (core_inputs["duration"][0], {**core_inputs["duration"][1], "min": 2})
                )
                # generate_audio: core's, for Seedance 1.5 Pro only.
                self.assertIn("generate_audio", core_inputs)
                self.assertNotIn("generate_audio", inputs)
                # Nothing else is missing.
                self.assertEqual(set(core_inputs) - set(inputs), REMOVED_CORE_INPUTS)

    def test_legacy_nodes_are_deprecated_but_unchanged(self):
        for node, name in (
            (nodes_video.BytePlusSeedance1, "BytePlus Seedance 1.0 (Legacy)"),
            (nodes_video.BytePlusSeedance1_5, "BytePlus Seedance 1.5 Pro (Legacy)"),
        ):
            schema = node.define_schema()
            self.assertTrue(schema.is_deprecated)
            self.assertEqual(schema.display_name, name)
            self.assertIn("enable_random_seed", [item.id for item in schema.inputs])


class _NodeRunner:
    """Runs a node's execute with a fake Ark client and the real executor."""

    def setUp(self):
        super().setUp()
        self.submitted = []
        self.quota_checks = []
        self._old_server = getattr(executor.PromptServer, "instance", None)
        executor.PromptServer.instance = SimpleNamespace(
            send_progress_text=lambda *_a, **_k: None, send_sync=lambda *_a, **_k: None
        )
        self._restore_hidden = []

    def tearDown(self):
        super().tearDown()
        if self._old_server is None:
            delattr(executor.PromptServer, "instance")
        else:
            executor.PromptServer.instance = self._old_server
        for node_cls, old_hidden in self._restore_hidden:
            if old_hidden is None:
                if "hidden" in node_cls.__dict__:
                    delattr(node_cls, "hidden")
            else:
                node_cls.hidden = old_hidden

    def _client(self, tasks=None):
        from byteplussdkarkruntime.resources.content_generation.tasks import Tasks as SdkTasks

        submitted = self.submitted

        class Tasks:
            @staticmethod
            def create(**kwargs):
                assert_matches_sdk(SdkTasks.create, kwargs)
                submitted.append(kwargs)
                return SimpleNamespace(id=f"cgt-{len(submitted)}")

            @staticmethod
            def list(**kwargs):
                assert_matches_sdk(SdkTasks.list, kwargs)
                return SimpleNamespace(items=[])

            @staticmethod
            def get(task_id):
                return tasks[task_id]

        return SimpleNamespace(
            ark=SimpleNamespace(content_generation=SimpleNamespace(tasks=Tasks())),
            check_quota=lambda model, cost: self.quota_checks.append((model, cost)),
            update_usage=lambda *_args: None,
        )

    def _set_hidden(self, node_cls, node_id, prompt):
        self._restore_hidden.append((node_cls, node_cls.__dict__.get("hidden")))
        node_cls.hidden = SimpleNamespace(unique_id=node_id, prompt=prompt)

    async def _run(self, node_cls, client=None, prompt_graph=None, node_id=None, **inputs):
        node_id = node_id or f"s1-{uuid.uuid4().hex[:8]}"
        self._set_hidden(node_cls, node_id, prompt_graph or {})
        inputs.setdefault("prompt", "a fox in the snow")
        inputs.setdefault("resolution", "720p")
        inputs.setdefault("aspect_ratio", "16:9")
        inputs.setdefault("duration", 5)
        inputs.setdefault("non_blocking", True)
        try:
            return await node_cls.execute(client or self._client(), **inputs)
        finally:
            nodes_video.NON_BLOCKING_TASK_CACHE.pop(node_id, None)


@requires_comfyui
class Seedance1RequestTests(_NodeRunner, unittest.IsolatedAsyncioTestCase):
    async def test_text_to_video_sends_json_fields(self):
        result = await self._run(
            T2V,
            model="seedance-1-0-pro-fast-251015",
            prompt="a fox in the snow",
            resolution="1080p",
            aspect_ratio="21:9",
            duration=7,
            seed=42,
            camera_fixed=True,
            watermark=True,
        )
        request = self.submitted[0]
        self.assertEqual(request["model"], "seedance-1-0-pro-fast-251015")
        self.assertEqual(request["content"], [{"type": "text", "text": "a fox in the snow"}])
        self.assertEqual(request["resolution"], "1080p")
        self.assertEqual(request["ratio"], "21:9")
        self.assertEqual(request["duration"], 7)
        self.assertEqual(request["seed"], 42)
        self.assertIs(request["camera_fixed"], True)
        self.assertIs(request["watermark"], True)
        self.assertTrue(request["return_last_frame"])
        self.assertEqual(request["service_tier"], "default")
        self.assertEqual(request["execution_expires_after"], 172800)
        # Seedance 1.0 has no audio and no draft mode (both were 1.5 Pro only).
        for absent in ("generate_audio", "draft", "frames"):
            self.assertNotIn(absent, request)
        self.assertEqual(self.quota_checks[0][0], "seedance-1-0-pro-fast-251015")

        # Pending non_blocking run: no video yet (nodes using it are skipped), task IDs in response.
        video, last_frame, response = result.args
        self.assertIsInstance(video, ExecutionBlocker)
        self.assertIsInstance(last_frame, ExecutionBlocker)
        self.assertEqual(json.loads(response)["task_ids"], ["cgt-1"])

    async def test_minimum_duration_is_two_seconds(self):
        """BytePlus allows 2-12 s for 1.0 Pro and 1.0 Pro Fast (core's widget starts at 3)."""
        frames = {
            T2V: {},
            I2V: {"image": _image(640, 360), "aspect_ratio": "adaptive"},
            FLF: {"first_frame": _image(640, 360), "last_frame": _image(640, 360), "aspect_ratio": "adaptive"},
        }
        for node in seedance1.NODES:
            options = next(i for i in node.define_schema().inputs if i.id == "model").options
            for model in options:
                with self.subTest(node=node.__name__, model=model):
                    self.submitted.clear()
                    await self._run(node, model=model, duration=2, **frames[node])
                    self.assertEqual(self.submitted[0]["model"], model)
                    self.assertEqual(self.submitted[0]["duration"], 2)
        self.assertEqual(models_config.SEEDANCE_1_MIN_DURATION, 2)

    async def test_optional_inputs_default_like_core(self):
        await self._run(T2V, model="seedance-1-0-pro-250528")
        request = self.submitted[0]
        self.assertEqual(request["model"], "seedance-1-0-pro-250528")
        self.assertEqual(request["seed"], 0)
        self.assertIs(request["camera_fixed"], False)
        self.assertIs(request["watermark"], False)

    async def test_image_to_video_sends_first_frame(self):
        await self._run(
            I2V,
            model="seedance-1-0-pro-250528",
            image=_image(640, 360),
            aspect_ratio="adaptive",
        )
        request = self.submitted[0]
        self.assertEqual(request["ratio"], "adaptive")
        text, frame = request["content"]
        self.assertEqual(text, {"type": "text", "text": "a fox in the snow"})
        self.assertEqual(frame["type"], "image_url")
        self.assertEqual(frame["role"], "first_frame")
        self.assertTrue(frame["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    async def test_first_last_frame_roles(self):
        await self._run(
            FLF,
            model="seedance-1-0-pro-250528",
            first_frame=_image(640, 360),
            last_frame=_image(360, 640),
            aspect_ratio="adaptive",
        )
        request = self.submitted[0]
        self.assertEqual(request["model"], "seedance-1-0-pro-250528")
        self.assertEqual(
            [item.get("role") for item in request["content"]], [None, "first_frame", "last_frame"]
        )
        self.assertNotIn("generate_audio", request)

    async def test_offline_inference_and_batch(self):
        await self._run(
            T2V,
            model="seedance-1-0-pro-fast-251015",
            enable_offline_inference=True,
            generation_count=3,
        )
        self.assertEqual(len(self.submitted), 3)
        self.assertEqual({r["service_tier"] for r in self.submitted}, {"flex"})

    async def test_batch_offsets_the_seed_per_task(self):
        # Tasks are submitted in parallel threads, so their order is not fixed.
        await self._run(T2V, model="seedance-1-0-pro-fast-251015", seed=10, generation_count=3)
        self.assertCountEqual([r["seed"] for r in self.submitted], [10, 11, 12])

    async def test_batch_seed_wraps_at_the_maximum(self):
        top = executor.VIDEO_MAX_SEED
        await self._run(T2V, model="seedance-1-0-pro-fast-251015", seed=top, generation_count=2)
        self.assertCountEqual([r["seed"] for r in self.submitted], [top, 0])

    async def test_single_generation_keeps_the_seed(self):
        await self._run(T2V, model="seedance-1-0-pro-fast-251015", seed=10, generation_count=1)
        self.assertEqual([r["seed"] for r in self.submitted], [10])

    async def test_batch_keeps_random_seed(self):
        # Legacy nodes send -1 (random) when enable_random_seed is on: every task stays random.
        await nodes_video.BytePlusVideoBase()._common_generation_logic(
            self._client(), "a fox in the snow", 5, "720p", "16:9", 10, 3, "test", False, True,
            f"s1-{uuid.uuid4().hex[:8]}",
            model_name="seedance-1-0-pro-fast-251015", content=[], forbidden_params=[],
            enable_random_seed=True,
        )
        self.assertEqual([r["seed"] for r in self.submitted], [-1, -1, -1])

    async def test_legacy_batch_keeps_the_same_seed(self):
        # The Legacy nodes (no list output) keep sending one seed to every task.
        await nodes_video.BytePlusVideoBase()._common_generation_logic(
            self._client(), "a fox in the snow", 5, "720p", "16:9", 10, 3, "test", False, True,
            f"s1-{uuid.uuid4().hex[:8]}",
            model_name="seedance-1-0-pro-fast-251015", content=[], forbidden_params=[],
            enable_random_seed=False,
        )
        self.assertEqual([r["seed"] for r in self.submitted], [10, 10, 10])


@requires_comfyui
class Seedance1ValidationTests(_NodeRunner, unittest.IsolatedAsyncioTestCase):
    async def _assert_rejected(self, node_cls, text, **inputs):
        with self.assertRaises(Exception) as ctx:
            await self._run(node_cls, **inputs)
        self.assertIn(text, str(ctx.exception))
        self.assertEqual(self.submitted, [])
        return ctx.exception

    async def test_prompt_must_not_be_empty(self):
        for prompt in ("", "   \n"):
            await self._assert_rejected(
                T2V, "prompt is empty", model="seedance-1-0-pro-250528", prompt=prompt
            )

    async def test_prompt_flags_are_rejected(self):
        for flag in ("--resolution 1080p", "--duration 5", "--watermark true", "--camerafixed true"):
            await self._assert_rejected(
                T2V, "is not allowed in the prompt",
                model="seedance-1-0-pro-250528", prompt=f"a fox {flag}",
            )

    async def test_deprecated_seedance_1_5_pro_is_rejected(self):
        """Core's third model: BytePlus shut it down on 2026-11-11."""
        frames = {
            T2V: {},
            I2V: {"image": _image(640, 360)},
            FLF: {"first_frame": _image(640, 360), "last_frame": _image(640, 360)},
        }
        for node in seedance1.NODES:
            with self.subTest(node=node.__name__):
                error = await self._assert_rejected(
                    node, f"does not support model {DEPRECATED_MODEL}",
                    model=DEPRECATED_MODEL, **frames[node],
                )
                self.assertTrue(str(error).startswith("[BytePlus]"))

    async def test_first_last_frame_has_no_pro_fast(self):
        await self._assert_rejected(
            FLF, "does not support model seedance-1-0-pro-fast-251015",
            model="seedance-1-0-pro-fast-251015",
            first_frame=_image(640, 360), last_frame=_image(640, 360),
        )

    async def test_frame_size_and_aspect_limits(self):
        for size, text in (
            ((299, 400), "image: width and height must be between 300 and 6000"),
            ((6001, 3000), "image: width and height must be between 300 and 6000"),
            ((1040, 400), "image: aspect ratio (width / height) must be between 0.4 and 2.5"),
            ((400, 1040), "image: aspect ratio"),
        ):
            await self._assert_rejected(
                I2V, text, model="seedance-1-0-pro-250528", image=_image(*size)
            )
        await self._assert_rejected(
            FLF, "last_frame: width and height",
            model="seedance-1-0-pro-250528",
            first_frame=_image(640, 360), last_frame=_image(200, 200),
        )
        # Both limits are inclusive (core: strict=False).
        for size in ((300, 750), (750, 300), (6000, 6000)):
            seedance1.validate_seedance1_frame(nodes_video.BytePlusVideoBase(), "image", _image(*size))


@requires_comfyui
class Seedance1OutputTests(_NodeRunner, unittest.IsolatedAsyncioTestCase):
    async def test_outputs_from_finished_tasks(self):
        video, frame = object(), object()
        response = json.dumps([{"id": "cgt-a"}, {"id": "cgt-b"}])
        calls = []

        async def fake_common(_self, *args, **kwargs):
            calls.append(kwargs)
            return comfy_io.NodeOutput(video, frame, response)

        old_common = nodes_video.BytePlusVideoBase._common_generation_logic
        nodes_video.BytePlusVideoBase._common_generation_logic = fake_common
        try:
            result = await self._run(T2V, model="seedance-1-0-pro-250528")
        finally:
            nodes_video.BytePlusVideoBase._common_generation_logic = old_common
        self.assertEqual(result.args, (video, frame, response))
        self.assertEqual(calls[0]["node_class_type"], "BytePlusSeedanceTextToVideo")
        self.assertIs(calls[0]["return_last_frame"], True)

    async def test_blocking_run_outputs(self):
        """Through the real executor and result handling (downloads stubbed)."""
        finished = SimpleNamespace(
            id="cgt-1",
            status="succeeded",
            seed=11,
            content=SimpleNamespace(video_url="https://example.invalid/v.mp4"),
            model_dump=lambda: {"id": "cgt-1", "status": "succeeded"},
        )
        stubs = {
            "download_video_to_temp": None,
            "extract_last_frame_tensor": None,
            "VideoFromFile": None,
        }

        async def fake_download(_session, url, _prefix, _seed, _folder):
            return f"/tmp/{os.path.basename(url)}"

        frame = _image(8, 8)
        replacements = {
            "download_video_to_temp": fake_download,
            "extract_last_frame_tensor": lambda _path: frame,
            "VideoFromFile": lambda path: ("video", path),
        }
        for name in stubs:
            stubs[name] = getattr(nodes_video, name)
            setattr(nodes_video, name, replacements[name])
        try:
            result = await self._run(
                T2V,
                client=self._client(tasks={"cgt-1": finished}),
                model="seedance-1-0-pro-fast-251015",
                non_blocking=False,
            )
        finally:
            for name, original in stubs.items():
                setattr(nodes_video, name, original)
        video, last_frame, response = result.args
        self.assertEqual(video, [("video", "/tmp/v.mp4")])  # list output
        self.assertTrue(torch.equal(last_frame, frame))
        self.assertEqual(json.loads(response)[0]["id"], "cgt-1")
        self.assertNotIn("draft", self.submitted[0])


@requires_comfyui
class NonBlockingRerunTests(unittest.TestCase):
    def test_non_blocking_runs_are_never_cached(self):
        import math

        nodes = [T2V, I2V, FLF] + [getattr(seedance2, n) for n in (
            "BytePlusSeedance2TextToVideo", "BytePlusSeedance2FirstLastFrame",
            "BytePlusSeedance2Reference", "BytePlusSeedanceDraftToFinal")]
        for node in nodes:
            with self.subTest(node=node.__name__):
                # "Run again to collect" must re-run the node even with a fixed seed.
                self.assertTrue(math.isnan(node.fingerprint_inputs(non_blocking=True, seed=1)))
                self.assertEqual(node.fingerprint_inputs(non_blocking=False, seed=1), 0)


@requires_comfyui
class Seedance1PollingTests(_NodeRunner, unittest.IsolatedAsyncioTestCase):
    """Polling ends on a permanent error or after repeated errors, instead of spinning forever."""

    class FailingTasks(dict):
        def __init__(self, error):
            super().__init__()
            self.error = error
            self.calls = 0

        def __getitem__(self, task_id):
            self.calls += 1
            raise self.error

    @staticmethod
    def status_error(cls_name, status):
        import httpx
        from byteplussdkarkruntime import _exceptions

        request = httpx.Request("GET", "https://ark.example/api/v3/contents/generations/tasks/cgt-1")
        return getattr(_exceptions, cls_name)(
            "error", response=httpx.Response(status, request=request), body=None, request_id="r1"
        )

    async def run_blocking(self, tasks):
        return await self._run(T2V, client=self._client(tasks=tasks), model="seedance-1-0-pro-fast-251015",
                               non_blocking=False, generation_count=1)

    async def test_permanent_poll_error_fails_at_once(self):
        tasks = self.FailingTasks(self.status_error("ArkNotFoundError", 404))
        with mock.patch.object(executor, "SEEDANCE_POLL_SECONDS", 0):
            with self.assertRaisesRegex(Exception, "Could not check task cgt-1"):
                await self.run_blocking(tasks)
        self.assertEqual(tasks.calls, 1)

    async def test_node_shows_poll_status(self):
        texts = []
        executor.PromptServer.instance = SimpleNamespace(
            send_progress_text=lambda text, node_id: texts.append((node_id, text)),
            send_sync=lambda *_a, **_k: None,
        )
        tasks = self.FailingTasks(RuntimeError("connection reset"))
        with mock.patch.object(executor, "SEEDANCE_POLL_SECONDS", 0), \
                mock.patch.object(executor, "SEEDANCE_MAX_POLL_ERRORS", 3):
            with self.assertRaises(Exception):
                await self.run_blocking(tasks)
        statuses = [text for _node, text in texts if "queued" in text]
        self.assertTrue(statuses)
        self.assertIn("0/1 done", statuses[0])
        self.assertNotIn("[BytePlus]", statuses[0])

    async def test_interrupt_deletes_pending_tasks(self):
        # Stopping the run must not leave paid tasks running on the account.
        import comfy.model_management as mm

        deleted = []

        class Tasks:
            @staticmethod
            def create(**kwargs):
                return SimpleNamespace(id="cgt-1")

            @staticmethod
            def list(**kwargs):
                return SimpleNamespace(items=[])

            @staticmethod
            def get(task_id):
                return SimpleNamespace(id=task_id, status="running")

            @staticmethod
            def delete(task_id):
                deleted.append(task_id)

        client = SimpleNamespace(
            ark=SimpleNamespace(content_generation=SimpleNamespace(tasks=Tasks())),
            check_quota=lambda *_a: None, update_usage=lambda *_a: None,
        )

        async def interrupt_soon():
            await asyncio.sleep(0.3)
            mm.interrupt_current_processing(True)

        waiter = asyncio.ensure_future(interrupt_soon())
        try:
            with mock.patch.object(executor, "SEEDANCE_POLL_SECONDS", 0.05):
                with self.assertRaises(mm.InterruptProcessingException):
                    await self.run_blocking_with(client)
        finally:
            mm.interrupt_current_processing(False)
            await waiter
        self.assertEqual(deleted, ["cgt-1"])

    async def run_blocking_with(self, client):
        return await self._run(T2V, client=client, model="seedance-1-0-pro-fast-251015",
                               non_blocking=False, generation_count=1)

    async def test_transient_poll_errors_are_capped(self):
        tasks = self.FailingTasks(RuntimeError("connection reset"))
        with mock.patch.object(executor, "SEEDANCE_POLL_SECONDS", 0), \
                mock.patch.object(executor, "SEEDANCE_MAX_POLL_ERRORS", 3):
            with self.assertRaisesRegex(Exception, "may still finish and be billed"):
                await self.run_blocking(tasks)
        self.assertEqual(tasks.calls, 3)


@requires_comfyui
class Seedance1BatchOutputTests(_NodeRunner, unittest.IsolatedAsyncioTestCase):
    """generation_count above 1: every video, paired last frames, and where videos are saved."""

    # Task ID -> seed; results are ordered by seed: cgt-2, cgt-3, cgt-1.
    SEEDS = {"cgt-1": 12, "cgt-2": 10, "cgt-3": 11}

    def setUp(self):
        super().setUp()
        self.saved = []
        self.frames = {f"/tmp/{tid}.mp4": torch.full((1, 8, 8, 3), seed / 100) for tid, seed in self.SEEDS.items()}
        replacements = {
            "download_video_to_temp": self._fake_download,
            "extract_last_frame_tensor": lambda path: self.frames.get(path),
            "VideoFromFile": lambda path: ("video", path),
            "save_to_output": lambda path, prefix: self.saved.append((path, prefix)),
        }
        for name, replacement in replacements.items():
            self.addCleanup(setattr, nodes_video, name, getattr(nodes_video, name))
            setattr(nodes_video, name, replacement)

    @staticmethod
    async def _fake_download(_session, url, _prefix, _seed, _folder):
        return f"/tmp/{os.path.basename(url)}"

    def _tasks(self):
        return [
            SimpleNamespace(
                id=tid,
                status="succeeded",
                seed=seed,
                content=SimpleNamespace(video_url=f"https://example.invalid/{tid}.mp4"),
                model_dump=lambda tid=tid: {"id": tid},
            )
            for tid, seed in self.SEEDS.items()
        ]

    async def _handle(self, as_list, generation_count=3, save_videos=None):
        return await nodes_video.BytePlusVideoBase()._handle_batch_success_async(
            self._tasks(), "BytePlus/Test", generation_count, False, None,
            as_list=as_list, save_videos=save_videos,
        )

    async def test_every_video_with_its_last_frame_in_seed_order(self):
        video, last_frame, response = (await self._handle(as_list=True)).args
        order = ["cgt-2", "cgt-3", "cgt-1"]
        self.assertEqual(video, [("video", f"/tmp/{tid}.mp4") for tid in order])
        self.assertEqual(tuple(last_frame.shape), (3, 8, 8, 3))
        for index, tid in enumerate(order):
            self.assertTrue(torch.equal(last_frame[index:index + 1], self.frames[f"/tmp/{tid}.mp4"]))
        self.assertEqual([item["id"] for item in json.loads(response)], order)

    async def test_missing_last_frame_is_left_out_of_the_batch(self):
        del self.frames["/tmp/cgt-2.mp4"]
        video, last_frame, _ = (await self._handle(as_list=True)).args
        self.assertEqual(len(video), 3)
        self.assertEqual(tuple(last_frame.shape), (2, 8, 8, 3))
        self.assertTrue(torch.equal(last_frame[0:1], self.frames["/tmp/cgt-3.mp4"]))

    async def test_legacy_outputs_pair_the_first_video_with_its_own_frame(self):
        video, last_frame, _ = (await self._handle(as_list=False)).args
        self.assertEqual(video, ("video", "/tmp/cgt-2.mp4"))
        self.assertTrue(torch.equal(last_frame, self.frames["/tmp/cgt-2.mp4"]))
        # Its frame missing: no frame, not the frame of another video.
        del self.frames["/tmp/cgt-2.mp4"]
        video, last_frame, _ = (await self._handle(as_list=False)).args
        self.assertEqual(video, ("video", "/tmp/cgt-2.mp4"))
        self.assertIsNone(last_frame)

    async def test_no_downloaded_video_blocks_the_list_outputs(self):
        async def no_video(*_args):
            return None

        nodes_video.download_video_to_temp = no_video
        for as_list in (True, False):
            with self.subTest(as_list=as_list):
                video, last_frame, response = (await self._handle(as_list=as_list)).args
                # Paid tasks with no downloadable video: blocked with the reason, not silently.
                for blocked in (video, last_frame):
                    self.assertIsInstance(blocked, ExecutionBlocker)
                    self.assertIn("cgt-1", blocked.message)
                    self.assertIn("24 hours", blocked.message)
                self.assertEqual(len(json.loads(response)), 3)  # the links are still in response

    async def test_single_video_is_not_saved_here(self):
        await self._handle(as_list=True, generation_count=1)
        self.assertEqual(self.saved, [])

    async def _run_batch(self, prompt_graph):
        return await self._run(
            T2V,
            client=self._client(tasks={task.id: task for task in self._tasks()}),
            prompt_graph=prompt_graph,
            node_id="7",
            model="seedance-1-0-pro-fast-251015",
            generation_count=3,
            non_blocking=False,
        )

    # Like core's nodes, the core-style nodes save nothing: every video reaches
    # the VIDEO output and Save Video keeps it. (One run per test: runs of the
    # same node share task state.)
    async def test_batch_into_save_video_is_not_saved_here(self):
        result = await self._run_batch({"8": {"class_type": "SaveVideo", "inputs": {"video": ["7", 0]}}})
        self.assertEqual(len(result.args[0]), 3)
        self.assertEqual(self.saved, [])

    async def test_batch_without_a_video_consumer_is_not_saved_either(self):
        # Only last_frame (output 1) is connected.
        result = await self._run_batch({"9": {"class_type": "PreviewImage", "inputs": {"images": ["7", 1]}}})
        self.assertEqual(len(result.args[0]), 3)
        self.assertEqual(self.saved, [])

    def test_only_legacy_batches_are_saved(self):
        # Legacy nodes output only the first video, so they keep writing the batch to the output folder.
        self.assertTrue(nodes_video._save_batch_videos(3, as_list=False))
        self.assertFalse(nodes_video._save_batch_videos(1, as_list=False))
        self.assertFalse(nodes_video._save_batch_videos(3, as_list=True))


@requires_comfyui
class Seedance1TemplateTests(unittest.TestCase):
    """example_workflows/Seedance 1.json against the live schema."""

    SOCKET_TYPES = {"BYTEPLUS_CLIENT", "IMAGE"}

    def test_template_matches_the_schema(self):
        path = os.path.join(PLUGIN_ROOT, "example_workflows", "Seedance 1.json")
        with open(path, encoding="utf-8") as file:
            workflow = json.load(file)
        classes = {node.NODE_ID: node for node in seedance1.NODES}
        checked = set()
        for node in workflow["nodes"]:
            node_cls = classes.get(node["type"])
            if node_cls is None:
                continue
            checked.add(node["type"])
            info = node_cls.GET_NODE_INFO_V1()
            with self.subTest(node=node["type"]):
                # The optional client first, then required inputs, then the other optional ones.
                specs = {
                    **{name: (spec, False) for name, spec in info["input"]["required"].items()},
                    **{name: (spec, True) for name, spec in info["input"]["optional"].items()},
                }
                expected = [(name, specs[name][0][0], specs[name][1]) for name in frontend_input_order(info)]
                self.assertEqual(
                    [(i["name"], i["type"], i.get("shape") == 7) for i in node["inputs"]], expected
                )
                for item in node["inputs"]:
                    self.assertEqual("widget" in item, item["type"] not in self.SOCKET_TYPES, item["name"])
                self.assertEqual([o["name"] for o in node["outputs"]], info["output_name"])
                self.assertEqual([o["type"] for o in node["outputs"]], info["output"])
                # widgets_values: one per widget input in schema order, plus the
                # seed's control_after_generate value right after the seed.
                widgets = [name for name, type_, _opt in expected if type_ not in self.SOCKET_TYPES]
                values = node["widgets_values"]
                seed = widgets.index("seed")
                self.assertEqual(len(values), len(widgets) + 1)
                self.assertIn(values[seed + 1], ("fixed", "randomize"))
                named = dict(zip(widgets[: seed + 1], values))
                named.update(zip(widgets[seed + 1 :], values[seed + 2 :]))
                model = next(i for i in node_cls.define_schema().inputs if i.id == "model")
                self.assertIn(named["model"], model.options)
                self.assertEqual(named["model"], model.default)
                self.assertTrue(named["prompt"].strip())
                self.assertIn(named["resolution"], ["480p", "720p", "1080p"])
                self.assertIsInstance(named["duration"], int)
                self.assertIsInstance(named["seed"], int)
                for name in ("camera_fixed", "watermark", "enable_offline_inference", "non_blocking"):
                    self.assertIsInstance(named[name], bool, name)
                self.assertIsInstance(named["generation_count"], int)
        self.assertEqual(checked, set(classes))


if __name__ == "__main__":
    unittest.main()
