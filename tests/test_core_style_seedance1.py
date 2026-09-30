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
    from comfy_api.latest import io as comfy_io  # noqa: E402

    T2V = seedance1.BytePlusSeedanceTextToVideo
    I2V = seedance1.BytePlusSeedanceImageToVideo
    FLF = seedance1.BytePlusSeedanceFirstLastFrame


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
    "seed", "camera_fixed", "watermark", "generate_audio",
]
EXTRA_INPUTS = [
    "auto_duration", "draft_mode", "enable_offline_inference", "generation_count",
    "filename_prefix", "save_last_frame_batch", "non_blocking",
]
CORE_ADVANCED = {"camera_fixed", "watermark", "generate_audio"}
CORE_OPTIONAL = {"seed", "camera_fixed", "watermark", "generate_audio"}


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
            # The frontend lists required inputs, then optional ones.
            order = info["input_order"]["required"] + info["input_order"]["optional"]
            with self.subTest(node=info["name"]):
                self.assertEqual(order, self.EXPECTED_INPUTS[info["name"]])
                self.assertEqual(
                    [item.id for item in node.define_schema().inputs],
                    self.EXPECTED_INPUTS[info["name"]],
                )

    def test_model_options_labels_and_ids(self):
        full = ["seedance-1-5-pro-251215", "seedance-1-0-pro-250528", "seedance-1-0-pro-fast-251015"]
        expected = {
            T2V: (full, "seedance-1-0-pro-fast-251015"),
            I2V: (full, "seedance-1-0-pro-fast-251015"),
            FLF: (full[:2], "seedance-1-5-pro-251215"),
        }
        for node, (options, default) in expected.items():
            model = node.define_schema().inputs[1]
            with self.subTest(node=node.__name__):
                self.assertEqual(model.id, "model")
                self.assertEqual(model.options, options)
                self.assertEqual(model.default, default)
        # Labels are core's; the values sent come from the BytePlus model map.
        self.assertEqual(
            models_config.SEEDANCE_1_MODELS,
            {
                "seedance-1-5-pro-251215": models_config.VIDEO_MODEL_MAP["seedance-1-5-pro"],
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
                self.assertEqual(
                    (duration.default, duration.min, duration.max, duration.step),
                    (5, 3, 12, 1),
                )
                self.assertEqual(duration.display_mode, comfy_io.NumberDisplay.slider)
                seed = inputs["seed"]
                self.assertEqual((seed.default, seed.min, seed.max), (0, 0, 2147483647))
                self.assertTrue(seed.control_after_generate)
                self.assertEqual(seed.tooltip, "Seed to use for generation.")
                self.assertTrue(inputs["prompt"].multiline)
                for name in ("camera_fixed", "watermark", "generate_audio"):
                    self.assertIs(inputs[name].default, False)
                for name in CORE_INPUTS:
                    self.assertEqual(bool(inputs[name].optional), name in CORE_OPTIONAL, name)
                    self.assertEqual(bool(inputs[name].advanced), name in CORE_ADVANCED, name)
                for name in EXTRA_INPUTS:
                    self.assertTrue(inputs[name].advanced, name)
                    self.assertTrue(inputs[name].optional, name)
                self.assertFalse(inputs["client"].optional)

        ratios = ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9"]
        self.assertEqual(T2V.define_schema().inputs[4].options, ratios)
        for node in (I2V, FLF):
            aspect = next(i for i in node.define_schema().inputs if i.id == "aspect_ratio")
            self.assertEqual(aspect.options, ["adaptive"] + ratios)

    def test_outputs(self):
        for node in seedance1.NODES:
            info = node.GET_NODE_INFO_V1()
            with self.subTest(node=info["name"]):
                self.assertEqual(info["output"], ["VIDEO", "STRING", "IMAGE", "STRING"])
                self.assertEqual(
                    info["output_name"], ["VIDEO", "draft_task_id", "last_frame", "response"]
                )
                self.assertEqual(seedance1.DRAFT_TASK_ID_OUTPUT, 1)

    def test_matches_core_nodes(self):
        """Core's inputs, compared as /object_info would show them."""
        try:
            core = importlib.import_module("comfy_api_nodes.nodes_bytedance")
        except Exception as e:  # e.g. an older ComfyUI or API nodes unavailable
            self.skipTest(f"comfy_api_nodes.nodes_bytedance unavailable: {e}")
        pairs = [
            (core.ByteDanceTextToVideoNode, T2V),
            (core.ByteDanceImageToVideoNode, I2V),
            (core.ByteDanceFirstLastFrameNode, FLF),
        ]
        for core_node, node in pairs:
            core_info, info = core_node.GET_NODE_INFO_V1(), node.GET_NODE_INFO_V1()
            core_order = core_info["input_order"]["required"] + core_info["input_order"]["optional"]
            order = info["input_order"]["required"] + info["input_order"]["optional"]
            with self.subTest(node=info["name"]):
                self.assertEqual(order[1 : 1 + len(core_order)], core_order)
                for section in ("required", "optional"):
                    for name, spec in core_info["input"][section].items():
                        self.assertEqual(info["input"][section][name], spec, name)
                self.assertEqual(info["output"][:1], core_info["output"])
                self.assertEqual(info["output_name"][:1], core_info["output_name"])
                self.assertEqual(
                    info["display_name"],
                    core_info["display_name"].replace("ByteDance", "BytePlus Seedance"),
                )

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
            generate_audio=True,
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
        # generate_audio is only sent for Seedance 1.5 Pro (ignored otherwise, like core).
        for absent in ("generate_audio", "draft", "frames"):
            self.assertNotIn(absent, request)
        self.assertEqual(self.quota_checks[0][0], "seedance-1-0-pro-fast-251015")

        # Pending non_blocking run: no video yet, empty draft_task_id, task IDs in response.
        video, draft_task_id, last_frame, response = result.args
        self.assertIsNone(video)
        self.assertEqual(draft_task_id, "")
        self.assertIsNone(last_frame)
        self.assertEqual(json.loads(response)["task_ids"], ["cgt-1"])

    async def test_seedance_1_5_sends_generate_audio(self):
        for generate_audio in (False, True):
            self.submitted.clear()
            await self._run(
                T2V, model="seedance-1-5-pro-251215", generate_audio=generate_audio, seed=3
            )
            request = self.submitted[0]
            self.assertEqual(request["model"], "seedance-1-5-pro-251215")
            self.assertIs(request["generate_audio"], generate_audio)
            self.assertIs(request["camera_fixed"], False)
            self.assertIs(request["watermark"], False)

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
            model="seedance-1-5-pro-251215",
            first_frame=_image(640, 360),
            last_frame=_image(360, 640),
            aspect_ratio="adaptive",
            generate_audio=True,
        )
        request = self.submitted[0]
        self.assertEqual(
            [item.get("role") for item in request["content"]], [None, "first_frame", "last_frame"]
        )
        self.assertIs(request["generate_audio"], True)

    async def test_draft_mode_request(self):
        await self._run(
            I2V,
            model="seedance-1-5-pro-251215",
            image=_image(640, 360),
            resolution="1080p",
            aspect_ratio="adaptive",
            draft_mode=True,
            enable_offline_inference=True,
        )
        request = self.submitted[0]
        self.assertIs(request["draft"], True)
        self.assertEqual(request["resolution"], "480p")
        self.assertFalse(request["return_last_frame"])
        # Drafts do not support offline inference.
        self.assertEqual(request["service_tier"], "default")

    async def test_auto_duration_sends_minus_one(self):
        await self._run(T2V, model="seedance-1-5-pro-251215", auto_duration=True, duration=3)
        self.assertEqual(self.submitted[0]["duration"], -1)

    async def test_offline_inference_and_batch(self):
        await self._run(
            T2V,
            model="seedance-1-0-pro-fast-251015",
            enable_offline_inference=True,
            generation_count=3,
        )
        self.assertEqual(len(self.submitted), 3)
        self.assertEqual({r["service_tier"] for r in self.submitted}, {"flex"})


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

    async def test_seedance_1_5_minimum_duration(self):
        error = await self._assert_rejected(
            T2V, "Minimum supported duration for Seedance 1.5 Pro is 4 seconds",
            model="seedance-1-5-pro-251215", duration=3,
        )
        self.assertTrue(str(error).startswith("[BytePlus]"))
        await self._run(T2V, model="seedance-1-0-pro-250528", duration=3)
        self.assertEqual(self.submitted[0]["duration"], 3)

    async def test_1_5_only_options(self):
        for option in ("auto_duration", "draft_mode"):
            await self._assert_rejected(
                T2V, f"{option} is only supported by seedance-1-5-pro-251215",
                model="seedance-1-0-pro-fast-251015", **{option: True},
            )

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
class Seedance1DraftOutputTests(_NodeRunner, unittest.IsolatedAsyncioTestCase):
    def _linked_graph(self, node_id, output_index):
        return {
            node_id: {"class_type": "BytePlusSeedanceTextToVideo", "inputs": {}},
            "20": {
                "class_type": "BytePlusSeedanceDraftToFinal",
                "inputs": {"draft_task_id": [node_id, output_index]},
            },
        }

    async def test_linked_draft_task_id_needs_draft_mode(self):
        with self.assertRaises(Exception) as ctx:
            await self._run(
                T2V,
                model="seedance-1-5-pro-251215",
                node_id="7",
                prompt_graph=self._linked_graph("7", 1),
            )
        message = str(ctx.exception)
        self.assertIn("Only draft_mode produces a draft_task_id", message)
        self.assertIn("BytePlusSeedanceDraftToFinal #20", message)
        self.assertEqual(message.count("[BytePlus]"), 1)
        self.assertEqual(self.submitted, [])

        # Linked in draft mode, or another output linked: fine.
        await self._run(
            T2V, model="seedance-1-5-pro-251215", draft_mode=True,
            node_id="7", prompt_graph=self._linked_graph("7", 1),
        )
        await self._run(
            T2V, model="seedance-1-5-pro-251215",
            node_id="8", prompt_graph=self._linked_graph("8", 0),
        )
        self.assertEqual(len(self.submitted), 2)

    async def test_outputs_from_finished_tasks(self):
        video, frame = object(), object()
        response = json.dumps([{"id": "cgt-a", "draft": True}, {"id": "cgt-b", "draft": True}])
        calls = []

        async def fake_common(_self, *args, **kwargs):
            calls.append(kwargs)
            return comfy_io.NodeOutput(video, frame, response)

        old_common = nodes_video.BytePlusVideoBase._common_generation_logic
        nodes_video.BytePlusVideoBase._common_generation_logic = fake_common
        try:
            draft = await self._run(T2V, model="seedance-1-5-pro-251215", draft_mode=True)
            normal = await self._run(T2V, model="seedance-1-5-pro-251215")
        finally:
            nodes_video.BytePlusVideoBase._common_generation_logic = old_common
        self.assertEqual(draft.args, (video, "cgt-a\ncgt-b", frame, response))
        self.assertEqual(normal.args, (video, "", frame, response))
        self.assertEqual(calls[0]["node_class_type"], "BytePlusSeedanceTextToVideo")

    async def test_blocking_draft_run_outputs_task_id(self):
        """Through the real executor and result handling (downloads stubbed)."""
        finished = SimpleNamespace(
            id="cgt-1",
            status="succeeded",
            seed=11,
            content=SimpleNamespace(video_url="https://example.invalid/v.mp4"),
            model_dump=lambda: {"id": "cgt-1", "status": "succeeded", "draft": True},
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
                model="seedance-1-5-pro-251215",
                draft_mode=True,
                non_blocking=False,
            )
        finally:
            for name, original in stubs.items():
                setattr(nodes_video, name, original)
        video, draft_task_id, last_frame, response = result.args
        self.assertEqual(video, ("video", "/tmp/v.mp4"))
        self.assertEqual(draft_task_id, "cgt-1")
        self.assertIs(last_frame, frame)
        self.assertEqual(json.loads(response)[0]["id"], "cgt-1")
        self.assertIs(self.submitted[0]["draft"], True)

    def test_draft_ids_from_response_shapes(self):
        parse = seedance1.draft_task_ids_from_response
        self.assertEqual(parse(json.dumps([{"id": "a"}, {"id": "b"}])), "a\nb")
        self.assertEqual(parse(json.dumps({"non_blocking": True, "task_ids": ["a"]})), "")
        self.assertEqual(parse(json.dumps({"error": "All tasks failed"})), "")
        self.assertEqual(parse(None), "")
        self.assertEqual(parse("not json"), "")


if __name__ == "__main__":
    unittest.main()
