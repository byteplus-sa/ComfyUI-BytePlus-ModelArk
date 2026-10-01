"""
Seedance 2 / 2.5 and asset nodes shaped like ComfyUI core's ByteDance nodes.

Needs a ComfyUI checkout and a Python env with torch, PyAV and the BytePlus SDK:
  COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_core_style_seedance2
The Ark SDK and the asset library are faked; nothing reaches the network.
"""
import base64
import importlib
import io
import json
import os
import sys
import time
import types
import unittest
from fractions import Fraction
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
    import utils  # noqa: F401, E402  (ComfyUI's utils package, before anything shadows it)

    PACKAGE_NAME = "byteplus_plugin_test"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [PLUGIN_ROOT]
        sys.modules[PACKAGE_NAME] = package

    models_config = importlib.import_module(f"{PACKAGE_NAME}.nodes.models_config")
    executor = importlib.import_module(f"{PACKAGE_NAME}.nodes.executor")
    nodes_video = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_video")
    nodes_assets = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_assets")
    nodes_seedance2 = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_seedance2")
    core_style = importlib.import_module(f"{PACKAGE_NAME}.nodes.core_style")
    nodes_shared = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_shared")

ASSET_ENV_KEYS = (
    "BYTEPLUS_ACCESS_KEY", "BYTEPLUS_SECRET_KEY", "BYTEPLUS_ACCESSKEY", "BYTEPLUS_SECRETKEY",
    "BYTEPLUS_SESSION_TOKEN",
)
EXTRAS = ["generation_count", "filename_prefix", "save_last_frame_batch", "non_blocking"]
MODEL_LABELS = [
    "Seedance 2.5", "Seedance 2.5 Draft", "Seedance 2.5 Premium", "Seedance 2.5 Premium Draft",
    "Seedance 2.0", "Seedance 2.0 Fast", "Seedance 2.0 Mini",
]
SEEDANCE25_TEXT = ["prompt", "resolution", "ratio", "duration", "generate_audio", "output_format"]
SEEDANCE20_TEXT = ["prompt", "resolution", "ratio", "duration", "generate_audio"]
REFERENCE_TAIL = ["reference_images", "reference_videos", "reference_audios", "reference_assets"]


def assert_matches_sdk(method, kwargs):
    """Bind request kwargs to the real SDK signature (fakes alone accept anything)."""
    import inspect

    fn = inspect.unwrap(method)
    for cell in fn.__closure__ or ():
        if inspect.isfunction(cell.cell_contents) and cell.cell_contents.__name__ == fn.__name__:
            fn = cell.cell_contents
    inspect.signature(fn).bind(None, **kwargs)


def v1_inputs(node_cls):
    info = node_cls.GET_NODE_INFO_V1()
    return info, dict(info["input"]["required"]), dict(info["input"].get("optional") or {})


def option_inputs(node_cls, label):
    _info, required, _optional = v1_inputs(node_cls)
    option = next(o for o in required["model"][1]["options"] if o["key"] == label)
    return option["inputs"]


def image(width, height, value=0.5):
    import torch

    return torch.full((1, height, width, 3), value)


def data_uri_size(uri):
    import PIL.Image

    raw = base64.b64decode(uri.split(",", 1)[1])
    return PIL.Image.open(io.BytesIO(raw)).size


def sine_audio(seconds, sample_rate=16000, channels=1):
    import torch

    t = torch.arange(int(seconds * sample_rate)) / sample_rate
    wave = torch.sin(2 * 3.14159 * 220 * t)[None].repeat(channels, 1)
    return {"waveform": wave[None] * 0.3, "sample_rate": sample_rate}


class FakeVideo:
    def __init__(self, width=1280, height=720, duration=5.0, fps=24):
        self.width, self.height, self.duration, self.fps = width, height, duration, fps

    def get_dimensions(self):
        return self.width, self.height

    def get_duration(self):
        return self.duration

    def get_frame_rate(self):
        return Fraction(self.fps)

    def get_stream_source(self):
        raise RuntimeError("no stream source")

    def get_active_trim_window(self):
        return 0.0, 0.0


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

@requires_comfyui
class SchemaTests(unittest.TestCase):
    def test_node_ids_and_display_names(self):
        expected = {
            nodes_seedance2.BytePlusSeedance2TextToVideo: ("BytePlusSeedance2TextToVideo", "BytePlus Seedance 2.5 Text to Video"),
            nodes_seedance2.BytePlusSeedance2FirstLastFrame: ("BytePlusSeedance2FirstLastFrame", "BytePlus Seedance 2.5 First-Last-Frame to Video"),
            nodes_seedance2.BytePlusSeedance2Reference: ("BytePlusSeedance2Reference", "BytePlus Seedance 2.5 Reference to Video"),
            nodes_seedance2.BytePlusSeedanceDraftToFinal: ("BytePlusSeedanceDraftToFinal", "BytePlus Seedance 2.5 Draft to Final Video"),
            nodes_assets.BytePlusCreateImageAsset: ("BytePlusCreateImageAsset", "BytePlus Create Image Asset"),
            nodes_assets.BytePlusCreateVideoAsset: ("BytePlusCreateVideoAsset", "BytePlus Create Video Asset"),
            nodes_assets.BytePlusCreateAudioAsset: ("BytePlusCreateAudioAsset", "BytePlus Create Audio Asset"),
        }
        for node_cls, (node_id, display_name) in expected.items():
            schema = node_cls.define_schema()
            self.assertEqual((schema.node_id, schema.display_name), (node_id, display_name))
            self.assertFalse(schema.is_api_node)
        self.assertEqual(list(nodes_seedance2.NODES), list(expected)[:4])
        self.assertEqual(list(nodes_assets.CORE_STYLE_NODES), list(expected)[4:])

    def test_legacy_nodes_are_deprecated_and_asset_library_is_not(self):
        legacy = nodes_video.BytePlusSeedance2.define_schema()
        self.assertTrue(legacy.is_deprecated)
        self.assertEqual(legacy.display_name, "BytePlus Seedance 2 / 2.5 (Legacy)")
        portrait = nodes_assets.BytePlusVirtualPortraitAsset.define_schema()
        self.assertTrue(portrait.is_deprecated)
        self.assertEqual(portrait.display_name, "BytePlus Virtual Portrait Asset (Legacy)")
        library = nodes_assets.BytePlusAssetLibrary.define_schema()
        self.assertFalse(library.is_deprecated)
        self.assertEqual(library.display_name, "BytePlus Asset Library")

    def test_top_level_input_order(self):
        # client first, core's inputs, then our extras (the FLF extras are optional
        # so they follow core's optional frame inputs in the UI too).
        cases = {
            nodes_seedance2.BytePlusSeedance2TextToVideo: (["client", "model", "seed", "watermark", *EXTRAS], []),
            nodes_seedance2.BytePlusSeedance2FirstLastFrame: (
                ["client", "model", "seed", "watermark"],
                ["first_frame", "last_frame", "first_frame_asset_id", "last_frame_asset_id", *EXTRAS],
            ),
            nodes_seedance2.BytePlusSeedance2Reference: (["client", "model", "seed", "watermark", *EXTRAS], []),
            nodes_seedance2.BytePlusSeedanceDraftToFinal: (["client", "draft_task_id", "watermark", *EXTRAS], []),
        }
        for node_cls, (required, optional) in cases.items():
            with self.subTest(node=node_cls.NODE_ID):
                _info, req, opt = v1_inputs(node_cls)
                self.assertEqual(list(req), required)
                self.assertEqual(list(opt), optional)
                for name in EXTRAS:
                    spec = (req.get(name) or opt.get(name))[1]
                    self.assertTrue(spec.get("advanced"), name)
                self.assertTrue((req.get("watermark") or opt.get("watermark"))[1].get("advanced"))

    def test_seed_is_core_seed_widget(self):
        _info, req, _opt = v1_inputs(nodes_seedance2.BytePlusSeedance2TextToVideo)
        seed = req["seed"][1]
        self.assertEqual((seed["default"], seed["min"], seed["max"]), (0, 0, 2147483647))
        self.assertTrue(seed["control_after_generate"])
        self.assertNotIn("enable_random_seed", req)

    def test_model_options_and_child_order(self):
        for node_cls in (
            nodes_seedance2.BytePlusSeedance2TextToVideo,
            nodes_seedance2.BytePlusSeedance2FirstLastFrame,
            nodes_seedance2.BytePlusSeedance2Reference,
        ):
            _info, req, _opt = v1_inputs(node_cls)
            self.assertEqual([o["key"] for o in req["model"][1]["options"]], MODEL_LABELS)
        for label in MODEL_LABELS:
            is_25 = "2.5" in label
            with self.subTest(label=label):
                t2v = option_inputs(nodes_seedance2.BytePlusSeedance2TextToVideo, label)
                self.assertEqual(list(t2v["required"]), SEEDANCE25_TEXT if is_25 else SEEDANCE20_TEXT)
                flf = option_inputs(nodes_seedance2.BytePlusSeedance2FirstLastFrame, label)
                expected_flf = [n for n in SEEDANCE25_TEXT if n != "ratio"] if is_25 else SEEDANCE20_TEXT
                self.assertEqual(list(flf["required"]), expected_flf)
                ref = option_inputs(nodes_seedance2.BytePlusSeedance2Reference, label)
                text = (
                    ["prompt", "resolution", "ratio", "duration", "generate_audio", "task_type", "output_format"]
                    if is_25 else SEEDANCE20_TEXT
                )
                self.assertEqual(list(ref["required"]), text + REFERENCE_TAIL)
                self.assertEqual(list(ref["optional"]), ["auto_downscale", "auto_upscale"])
                self.assertTrue(ref["optional"]["auto_upscale"][1]["advanced"])
                self.assertFalse(ref["optional"]["auto_downscale"][1].get("advanced", False))

    def test_resolutions_durations_and_defaults(self):
        expected = {
            "Seedance 2.5": (["480p", "720p", "1080p"], "720p", 30, 5),
            "Seedance 2.5 Draft": (["480p"], "480p", 30, 5),
            "Seedance 2.5 Premium": (["4k"], "4k", 30, 5),
            "Seedance 2.5 Premium Draft": (["480p"], "480p", 30, 5),
            "Seedance 2.0": (["480p", "720p", "1080p", "4k"], None, 15, 7),
            "Seedance 2.0 Fast": (["480p", "720p"], None, 15, 7),
            "Seedance 2.0 Mini": (["480p", "720p"], None, 15, 7),
        }
        for label, (resolutions, default, max_duration, default_duration) in expected.items():
            with self.subTest(label=label):
                req = option_inputs(nodes_seedance2.BytePlusSeedance2TextToVideo, label)["required"]
                self.assertEqual(req["resolution"][1]["options"], resolutions)
                self.assertEqual(req["resolution"][1].get("default"), default)
                self.assertEqual(req["duration"][1]["max"], max_duration)
                self.assertEqual(req["duration"][1]["default"], default_duration)
                self.assertEqual(req["ratio"][1]["default"], "16:9")
                flf = option_inputs(nodes_seedance2.BytePlusSeedance2FirstLastFrame, label)["required"]
                if "ratio" in flf:
                    self.assertEqual(flf["ratio"][1]["default"], "adaptive")

    def test_reference_autogrow_caps_per_model(self):
        for label in MODEL_LABELS:
            # asset_N covers every reference type: 50 in total on 2.5, 15 on 2.0.
            caps = (30, 10, 10, 50) if "2.5" in label else (9, 3, 3, 15)
            with self.subTest(label=label):
                req = option_inputs(nodes_seedance2.BytePlusSeedance2Reference, label)["required"]
                names = []
                for key, prefix in (
                    ("reference_images", "image"),
                    ("reference_videos", "video"),
                    ("reference_audios", "audio"),
                    ("reference_assets", "asset"),
                ):
                    template = req[key][1]["template"]
                    self.assertEqual(template["names"][0], f"{prefix}_1")
                    self.assertEqual(template["min"], 0)
                    names.append(len(template["names"]))
                self.assertEqual(tuple(names), caps)

    def test_outputs(self):
        for node_cls in (
            nodes_seedance2.BytePlusSeedance2TextToVideo,
            nodes_seedance2.BytePlusSeedance2FirstLastFrame,
            nodes_seedance2.BytePlusSeedance2Reference,
        ):
            info = node_cls.GET_NODE_INFO_V1()
            self.assertEqual(info["output"], ["VIDEO", "STRING", "IMAGE", "STRING"])
            self.assertEqual(info["output_name"], ["VIDEO", "draft_task_id", "last_frame", "response"])
        info = nodes_seedance2.BytePlusSeedanceDraftToFinal.GET_NODE_INFO_V1()
        self.assertEqual(info["output_name"], ["VIDEO", "last_frame", "response"])
        for node_cls in nodes_assets.CORE_STYLE_NODES:
            info = node_cls.GET_NODE_INFO_V1()
            self.assertEqual(info["output_name"], ["asset_id", "group_id", "asset_uri", "info"])

    def test_asset_node_inputs(self):
        for node_cls, media, url_input in (
            (nodes_assets.BytePlusCreateImageAsset, "image", "image_url"),
            (nodes_assets.BytePlusCreateVideoAsset, "video", "video_url"),
            (nodes_assets.BytePlusCreateAudioAsset, "audio", "audio_url"),
        ):
            with self.subTest(node=node_cls.NODE_ID):
                _info, req, opt = v1_inputs(node_cls)
                self.assertEqual(
                    list(req),
                    ["client", "group_id", url_input, "group_name", "asset_name", "project_name", "wait_until_active"],
                )
                self.assertEqual(list(opt), [media])
                self.assertFalse(req["group_id"][1].get("advanced", False))
                for name in (url_input, "group_name", "asset_name", "project_name", "wait_until_active"):
                    self.assertTrue(req[name][1]["advanced"], name)
                schema = node_cls.define_schema()
                self.assertEqual([i.id for i in schema.inputs][:3], ["client", media, "group_id"])

    def test_matches_core_nodes(self):
        try:
            core = importlib.import_module("comfy_api_nodes.nodes_bytedance")
        except Exception as e:  # pragma: no cover - depends on the ComfyUI checkout
            self.skipTest(f"core ByteDance nodes unavailable: {e}")
        pairs = [
            (nodes_seedance2.BytePlusSeedance2TextToVideo, core.ByteDance2TextToVideoNode),
            (nodes_seedance2.BytePlusSeedance2FirstLastFrame, core.ByteDance2FirstLastFrameNode),
            (nodes_seedance2.BytePlusSeedance2Reference, core.ByteDance2ReferenceNodeV2),
            (nodes_seedance2.BytePlusSeedanceDraftToFinal, core.ByteDance2DraftToFinalVideoNode),
        ]
        # Deliberate differences: mov output, our tooltips on inputs that accept more.
        allowed = {
            ("output_format", "options"),
            ("reference_assets", "tooltip"),
            ("reference_assets", "template"),
            ("first_frame_asset_id", "tooltip"),
            ("last_frame_asset_id", "tooltip"),
            ("draft_task_id", "tooltip"),
        }

        def compare(where, name, ours, theirs):
            self.assertEqual(ours[0], theirs[0], where)
            a, b = (ours[1] if len(ours) > 1 else {}), (theirs[1] if len(theirs) > 1 else {})
            if name == "output_format":
                # Ours adds mov after core's mp4.
                self.assertEqual(a["options"][: len(b["options"])], b["options"], where)
            if name == "reference_assets":
                # Ours extends core's slots (image cap) to the BytePlus total (15 / 50).
                core_names = b["template"]["names"]
                self.assertEqual(a["template"]["names"][: len(core_names)], core_names, where)
                self.assertGreater(len(a["template"]["names"]), len(core_names), where)
            for key in set(a) | set(b):
                if (name, key) in allowed or key == "options" and name == "model":
                    continue
                self.assertEqual(a.get(key), b.get(key), f"{where}.{key}")

        for ours_cls, core_cls in pairs:
            _i, req, opt = v1_inputs(ours_cls)
            ours = {**req, **opt}
            core_info = core_cls.GET_NODE_INFO_V1()
            theirs_req = core_info["input"]["required"]
            theirs_opt = core_info["input"].get("optional") or {}
            theirs = {**theirs_req, **theirs_opt}
            self.assertEqual([n for n in req if n not in ["client", *EXTRAS]], list(theirs_req))
            self.assertEqual([n for n in opt if n not in EXTRAS], list(theirs_opt))
            for name, spec in theirs.items():
                if name == "model":
                    ours_options = {o["key"]: o["inputs"] for o in ours[name][1]["options"]}
                    for option in spec[1]["options"]:
                        key = option["key"]
                        mine = ours_options[key]
                        for section in ("required", "optional"):
                            core_children = option["inputs"].get(section) or {}
                            my_children = mine.get(section) or {}
                            self.assertEqual(list(my_children), list(core_children), f"{key} {section}")
                            for child, child_spec in core_children.items():
                                compare(f"{key}.{child}", child, my_children[child], child_spec)
                    continue
                compare(f"{ours_cls.NODE_ID}.{name}", name, ours[name], spec)
            our_outputs = ours_cls.GET_NODE_INFO_V1()
            n = len(core_info["output"])
            self.assertEqual(our_outputs["output"][:n], core_info["output"])
            self.assertEqual(our_outputs["output_name"][:n], core_info["output_name"])

        for ours_cls, core_cls in (
            (nodes_assets.BytePlusCreateImageAsset, core.ByteDanceCreateImageAsset),
            (nodes_assets.BytePlusCreateVideoAsset, core.ByteDanceCreateVideoAsset),
        ):
            core_info = core_cls.GET_NODE_INFO_V1()
            info = ours_cls.GET_NODE_INFO_V1()
            self.assertEqual(info["output_name"][:2], core_info["output_name"])
            core_inputs = [i.id for i in core_cls.define_schema().inputs]
            our_inputs = [i.id for i in ours_cls.define_schema().inputs]
            self.assertEqual(our_inputs[1 : 1 + len(core_inputs)], core_inputs)


@requires_comfyui
class TemplateSchemaTests(unittest.TestCase):
    SOCKET_TYPES = {"BYTEPLUS_CLIENT", "IMAGE", "VIDEO", "AUDIO"}

    @staticmethod
    def frontend_order(node_cls, label):
        """Input names as the frontend lists them: required, then optional; DynamicCombo children after it."""
        info = node_cls.GET_NODE_INFO_V1()
        names = []
        for section in ("required", "optional"):
            for name, spec in (info["input"].get(section) or {}).items():
                names.append((name, spec[0]))
                if spec[0] == "COMFY_DYNAMICCOMBO_V3":
                    option = next(o for o in spec[1]["options"] if o["key"] == label)
                    for sub in ("required", "optional"):
                        for child, child_spec in (option["inputs"].get(sub) or {}).items():
                            if child_spec[0] != "COMFY_AUTOGROW_V3":
                                names.append((f"{name}.{child}", child_spec[0]))
        return names

    def test_seedance2_template_matches_the_schema(self):
        with open(os.path.join(PLUGIN_ROOT, "example_workflows", "Seedance 2.json"), encoding="utf-8") as file:
            workflow = json.load(file)
        classes = {cls.NODE_ID: cls for cls in nodes_seedance2.NODES}
        checked = set()
        for node in workflow["nodes"]:
            node_cls = classes.get(node["type"])
            if node_cls is None:
                continue
            checked.add(node["type"])
            label = node["widgets_values"][0] if node["type"] != "BytePlusSeedanceDraftToFinal" else None
            expected = self.frontend_order(node_cls, label)
            inputs = [i for i in node["inputs"] if not i["name"].startswith("model.reference_")]
            self.assertEqual([i["name"] for i in inputs], [name for name, _type in expected], node["type"])
            for item, (_name, type_) in zip(inputs, expected):
                self.assertEqual("widget" in item, type_ not in self.SOCKET_TYPES, item["name"])
            info = node_cls.GET_NODE_INFO_V1()
            self.assertEqual([o["name"] for o in node["outputs"]], info["output_name"])
            self.assertEqual([o["type"] for o in node["outputs"]], info["output"])
        self.assertEqual(checked, set(classes))


# --------------------------------------------------------------------------
# Requests through the real executor with a fake Ark client
# --------------------------------------------------------------------------

class _ExecutorHarness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from byteplussdkarkruntime.resources.content_generation.tasks import Tasks as SdkTasks

        self.submitted = []
        self.task_lookups = {}
        test = self

        class Tasks:
            @staticmethod
            def create(**kwargs):
                assert_matches_sdk(SdkTasks.create, kwargs)
                test.submitted.append(kwargs)
                return SimpleNamespace(id=f"cgt-{len(test.submitted)}")

            @staticmethod
            def list(**kwargs):
                assert_matches_sdk(SdkTasks.list, kwargs)
                return SimpleNamespace(items=[])

            @staticmethod
            def get(**kwargs):
                assert_matches_sdk(SdkTasks.get, kwargs)
                task = test.task_lookups[kwargs["task_id"]]
                if isinstance(task, Exception):
                    raise task
                return task

        self.client = SimpleNamespace(
            ark=SimpleNamespace(content_generation=SimpleNamespace(tasks=Tasks())),
            check_quota=lambda *_a: None,
            update_usage=lambda *_a: None,
            region="ap-southeast-1",
            asset_credentials=None,
        )
        self._old_server = getattr(executor.PromptServer, "instance", None)
        executor.PromptServer.instance = SimpleNamespace(
            send_progress_text=lambda *_a, **_k: None, send_sync=lambda *_a, **_k: None
        )
        self._env = mock.patch.dict(os.environ, {})
        self._env.start()
        for key in ASSET_ENV_KEYS:
            os.environ.pop(key, None)
        nodes_video.NON_BLOCKING_TASK_CACHE.clear()
        nodes_video.COMFY_VIDEO_UPLOAD_CACHE.clear()
        self._hidden = {}

    def tearDown(self):
        self._env.stop()
        if self._old_server is None:
            delattr(executor.PromptServer, "instance")
        else:
            executor.PromptServer.instance = self._old_server
        for node_cls, old in self._hidden.items():
            if old is None:
                delattr(node_cls, "hidden")
            else:
                node_cls.hidden = old

    def set_hidden(self, node_cls, unique_id="5", prompt=None):
        if node_cls not in self._hidden:
            self._hidden[node_cls] = node_cls.__dict__.get("hidden")
        node_cls.hidden = SimpleNamespace(unique_id=unique_id, prompt=prompt or {})

    async def run_node(self, node_cls, *, model=None, non_blocking=True, prompt=None, **inputs):
        self.set_hidden(node_cls, prompt=prompt)
        kwargs = dict(inputs)
        if model is not None:
            kwargs["model"] = model
        kwargs.setdefault("non_blocking", non_blocking)
        return await node_cls.execute(self.client, **kwargs)


@requires_comfyui
class TextToVideoTests(_ExecutorHarness):
    T2V = None

    def setUp(self):
        super().setUp()
        self.T2V = nodes_seedance2.BytePlusSeedance2TextToVideo

    def model(self, label="Seedance 2.5", **overrides):
        values = {"model": label, "prompt": "a fox in the snow", "resolution": "720p", "ratio": "16:9",
                  "duration": 5, "generate_audio": True}
        if "2.5" in label:
            values["output_format"] = "mp4"
        values.update(overrides)
        return values

    async def test_seedance25_request(self):
        result = await self.run_node(self.T2V, model=self.model(), seed=42, watermark=False)
        request = self.submitted[0]
        self.assertEqual(request["model"], "dreamina-seedance-2-5-260628")
        self.assertEqual(request["content"], [{"type": "text", "text": "a fox in the snow"}])
        self.assertEqual(
            {k: request[k] for k in ("resolution", "ratio", "duration", "seed", "watermark", "generate_audio", "output_format")},
            {"resolution": "720p", "ratio": "16:9", "duration": 5, "seed": 42, "watermark": False,
             "generate_audio": True, "output_format": "mp4"},
        )
        self.assertTrue(request["return_last_frame"])
        for absent in ("draft", "omni_reference_task_type", "service_tier", "execution_expires_after"):
            self.assertNotIn(absent, request)
        self.assertEqual(result.args[1], "cgt-1")
        self.assertIsNone(result.args[0])
        self.assertEqual(json.loads(result.args[3])["task_ids"], ["cgt-1"])

    async def test_seed_zero_and_watermark_are_sent(self):
        await self.run_node(self.T2V, model=self.model(), seed=0, watermark=True)
        self.assertEqual((self.submitted[0]["seed"], self.submitted[0]["watermark"]), (0, True))

    async def test_draft_options(self):
        result = await self.run_node(
            self.T2V, model=self.model("Seedance 2.5 Draft", resolution="480p"), seed=1, generation_count=2
        )
        self.assertEqual(len(self.submitted), 2)
        for request in self.submitted:
            self.assertTrue(request["draft"])
            self.assertEqual(request["resolution"], "480p")
            self.assertFalse(request["return_last_frame"])
            self.assertEqual(request["model"], "dreamina-seedance-2-5-260628")
        self.assertEqual(result.args[1], "cgt-1\ncgt-2")
        self.submitted.clear()
        nodes_video.NON_BLOCKING_TASK_CACHE.clear()
        await self.run_node(self.T2V, model=self.model("Seedance 2.5 Premium Draft", resolution="480p"), seed=1)
        self.assertEqual(self.submitted[0]["model"], "dreamina-seedance-2-5-premium-260915")
        self.assertTrue(self.submitted[0]["draft"])

    async def test_premium_is_4k_only(self):
        await self.run_node(self.T2V, model=self.model("Seedance 2.5 Premium", resolution="4k", duration=30), seed=1)
        self.assertEqual(self.submitted[0]["model"], "dreamina-seedance-2-5-premium-260915")
        self.assertEqual((self.submitted[0]["resolution"], self.submitted[0]["duration"]), ("4k", 30))
        with self.assertRaises(Exception):
            await self.run_node(self.T2V, model=self.model("Seedance 2.5 Premium", resolution="1080p"), seed=1)

    async def test_seedance20_request(self):
        model = {"model": "Seedance 2.0", "prompt": "city", "resolution": "4k", "ratio": "adaptive",
                 "duration": 7, "generate_audio": False}
        await self.run_node(self.T2V, model=model, seed=3)
        request = self.submitted[0]
        self.assertEqual(request["model"], "dreamina-seedance-2-0-260128")
        self.assertEqual((request["resolution"], request["ratio"], request["duration"]), ("4k", "adaptive", 7))
        self.assertFalse(request["generate_audio"])
        self.assertNotIn("output_format", request)
        with self.assertRaises(Exception):
            await self.run_node(self.T2V, model={**model, "duration": 16}, seed=3)
        with self.assertRaises(Exception):
            await self.run_node(self.T2V, model={**model, "model": "Seedance 2.0 Fast"}, seed=3)

    async def test_empty_prompt_and_prompt_flags(self):
        with self.assertRaises(Exception) as ctx:
            await self.run_node(self.T2V, model=self.model(prompt="   "), seed=1)
        self.assertIn("Enter a prompt", str(ctx.exception))
        with self.assertRaises(Exception):
            await self.run_node(self.T2V, model=self.model(prompt="fox --ratio 1:1"), seed=1)
        self.assertEqual(self.submitted, [])

    async def test_linked_draft_task_id_needs_a_draft_model(self):
        prompt = {"9": {"class_type": "BytePlusSeedanceDraftToFinal", "inputs": {"draft_task_id": ["5", 1]}}}
        with self.assertRaises(Exception) as ctx:
            await self.run_node(self.T2V, model=self.model(), seed=1, prompt=prompt)
        self.assertIn("draft_task_id", str(ctx.exception))
        self.assertIn("BytePlusSeedanceDraftToFinal #9", str(ctx.exception))
        self.assertEqual(self.submitted, [])
        # Linked video output only: fine.
        video_only = {"9": {"class_type": "SaveVideo", "inputs": {"video": ["5", 0]}}}
        await self.run_node(self.T2V, model=self.model(), seed=1, prompt=video_only)
        nodes_video.NON_BLOCKING_TASK_CACHE.clear()
        await self.run_node(self.T2V, model=self.model("Seedance 2.5 Draft", resolution="480p"), seed=1, prompt=prompt)
        self.assertEqual(len(self.submitted), 2)

    async def test_blocking_run_outputs_successful_task_ids(self):
        tasks = {}

        def get(**kwargs):
            return tasks[kwargs["task_id"]]

        original_create = self.client.ark.content_generation.tasks.create

        def create(**kwargs):
            created = original_create(**kwargs)
            tasks[created.id] = SimpleNamespace(id=created.id, status="succeeded")
            return created

        self.client.ark.content_generation.tasks = SimpleNamespace(
            create=create, get=get, list=lambda **_k: SimpleNamespace(items=[])
        )

        async def fake_success(_self, successful, *_args, **_kwargs):
            return nodes_video.comfy_io.NodeOutput(
                "video", "frame", json.dumps([{"id": t.id} for t in successful])
            )

        with mock.patch.object(nodes_video.BytePlusVideoBase, "_handle_batch_success_async", fake_success):
            result = await self.run_node(
                self.T2V, model=self.model("Seedance 2.5 Draft", resolution="480p"), seed=1,
                generation_count=2, non_blocking=False,
            )
        self.assertEqual(result.args[0], "video")
        self.assertEqual(result.args[1], "cgt-1\ncgt-2")
        self.assertEqual(result.args[2], "frame")


@requires_comfyui
class FirstLastFrameTests(_ExecutorHarness):
    def setUp(self):
        super().setUp()
        self.FLF = nodes_seedance2.BytePlusSeedance2FirstLastFrame

    def model(self, label="Seedance 2.0", **overrides):
        values = {"model": label, "prompt": "walk", "resolution": "1080p", "duration": 5, "generate_audio": True}
        if "2.5" in label:
            values["output_format"] = "mp4"
        else:
            values["ratio"] = "16:9"
        values.update(overrides)
        return values

    def frames(self):
        return [item for item in self.submitted[0]["content"] if item["type"] == "image_url"]

    async def test_seedance20_local_frames_are_presized_and_adaptive(self):
        await self.run_node(
            self.FLF, model=self.model(), seed=1, first_frame=image(1000, 800), last_frame=image(640, 640)
        )
        request = self.submitted[0]
        self.assertEqual(request["ratio"], "adaptive")
        frames = self.frames()
        self.assertEqual([f["role"] for f in frames], ["first_frame", "last_frame"])
        self.assertEqual(data_uri_size(frames[0]["image_url"]["url"]), (1920, 1080))
        self.assertEqual(data_uri_size(frames[1]["image_url"]["url"]), (1920, 1080))
        self.assertEqual(request["content"][0], {"type": "text", "text": "walk"})

    async def test_adaptive_ratio_snaps_to_frame_aspect(self):
        self.assertEqual(nodes_seedance2.seedance2_target_dims("720p", "adaptive", image(600, 1000)), (720, 1280))
        self.assertEqual(nodes_seedance2.seedance2_target_dims("4k", "21:9", image(600, 600)), (5040, 2160))
        self.assertEqual(nodes_seedance2.seedance2_target_dims("480p", "4:3", image(600, 600)), (640, 480))

    async def test_seedance25_keeps_frame_and_forces_adaptive(self):
        await self.run_node(self.FLF, model=self.model("Seedance 2.5"), seed=1, first_frame=image(1000, 800))
        request = self.submitted[0]
        self.assertEqual(request["ratio"], "adaptive")
        self.assertEqual(data_uri_size(self.frames()[0]["image_url"]["url"]), (1000, 800))
        self.submitted.clear()
        nodes_video.NON_BLOCKING_TASK_CACHE.clear()
        await self.run_node(
            self.FLF, model=self.model("Seedance 2.5 Premium", resolution="4k"), seed=1, first_frame=image(1000, 800)
        )
        self.assertEqual(self.submitted[0]["ratio"], "adaptive")
        self.assertEqual(self.submitted[0]["resolution"], "4k")

    async def test_asset_ids_and_links(self):
        await self.run_node(
            self.FLF, model=self.model(ratio="9:16"), seed=1,
            first_frame_asset_id=" asset-20260101-abc ", last_frame_asset_id="https://cdn.example/end.png",
        )
        request = self.submitted[0]
        self.assertEqual(request["ratio"], "9:16")  # core: asset frames keep the chosen ratio
        self.assertEqual(
            self.frames(),
            [
                {"type": "image_url", "image_url": {"url": "asset://asset-20260101-abc"}, "role": "first_frame"},
                {"type": "image_url", "image_url": {"url": "https://cdn.example/end.png"}, "role": "last_frame"},
            ],
        )

    async def test_asset_type_is_checked_with_credentials(self):
        self.client.asset_credentials = {"access_key": "AK", "secret_key": "SK"}

        class FakeLibrary:
            def __init__(self, client):
                pass

            def call(self, action, body):
                return {"Id": body["Id"], "Status": "Active", "AssetType": "Video"}

        with mock.patch.object(nodes_assets, "AssetLibrary", FakeLibrary):
            with self.assertRaises(Exception) as ctx:
                await self.run_node(self.FLF, model=self.model(), seed=1, first_frame_asset_id="asset-video")
        self.assertIn("needs image", str(ctx.exception))
        with self.assertRaises(Exception) as ctx:
            await self.run_node(self.FLF, model=self.model(), seed=1, first_frame_asset_id="https://cdn.example/a.mp4")
        self.assertIn("needs image", str(ctx.exception))

    async def test_frame_input_rules(self):
        cases = [
            ({"first_frame": image(640, 640), "first_frame_asset_id": "asset-1"}, "not both"),
            ({}, "is required"),
            ({"first_frame": image(640, 640), "last_frame": image(640, 640), "last_frame_asset_id": "asset-2"}, "not both"),
            ({"first_frame": image(200, 640)}, "ratio"),
            ({"first_frame": image(640, 200)}, "ratio"),
            ({"first_frame": image(280, 280)}, "between"),
        ]
        for inputs, message in cases:
            with self.subTest(inputs=list(inputs)):
                with self.assertRaises(Exception) as ctx:
                    await self.run_node(self.FLF, model=self.model(), seed=1, **inputs)
                self.assertIn(message, str(ctx.exception))
        self.assertEqual(self.submitted, [])

    async def test_large_frames_are_downscaled_for_seedance25(self):
        prepared = nodes_seedance2.prepare_seedance_image(image(7000, 3500))
        self.assertEqual(nodes_seedance2._image_size(prepared), (6000, 3000))


@requires_comfyui
class ReferenceTests(_ExecutorHarness):
    def setUp(self):
        super().setUp()
        self.REF = nodes_seedance2.BytePlusSeedance2Reference
        self.uploads = []
        self.asset_types = {}
        self.get_asset_calls = []
        test = self

        async def fake_upload(_cls, video, **keys):
            test.uploads.append((video, keys))
            return f"https://storage.example/ref-{len(test.uploads)}.mp4"

        class FakeLibrary:
            def __init__(self, client):
                nodes_assets.resolve_asset_credentials(client)

            def call(self, action, body):
                test.get_asset_calls.append((action, body))
                kind, status = test.asset_types[body["Id"]]
                return {"Id": body["Id"], "Status": status, "AssetType": kind}

        self._patches = [
            mock.patch.object(nodes_video, "upload_video_to_comfy_storage", fake_upload),
            mock.patch.object(nodes_assets, "AssetLibrary", FakeLibrary),
        ]
        for patcher in self._patches:
            patcher.start()

    def tearDown(self):
        for patcher in self._patches:
            patcher.stop()
        super().tearDown()

    def model(self, label="Seedance 2.5", **overrides):
        values = {"model": label, "prompt": "asset1 dances with the girl from Image 1", "resolution": "720p",
                  "ratio": "16:9", "duration": 5, "generate_audio": True, "auto_downscale": True,
                  "auto_upscale": False, "reference_images": {}, "reference_videos": {},
                  "reference_audios": {}, "reference_assets": {}}
        if "2.5" in label:
            values.update(task_type="auto", output_format="mp4")
        values.update(overrides)
        return values

    async def test_reference_values_are_one_per_slot(self):
        # The Asset Library's asset_uris output is newline-joined: one slot cannot take a list.
        for value in ("asset://a\nasset://b", "https://cdn.example/a.png https://cdn.example/b.png", "asset-1 asset-2"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(Exception, "Connect each reference to its own asset_N slot"):
                    core_style.split_reference_value(value)
        self.assertEqual(core_style.split_reference_value("  asset://asset-1  "), ("asset", "asset-1"))

    async def test_asset_lookups_share_one_client(self):
        self.client.asset_credentials = {"access_key": "AK", "secret_key": "SK"}
        self.asset_types = {f"asset-{n}": ("Image", "Active") for n in range(6)}
        created = []
        original = nodes_assets.AssetLibrary

        class CountingLibrary(original):
            def __init__(self, client):
                created.append(client)
                super().__init__(client)

        with mock.patch.object(nodes_assets, "AssetLibrary", CountingLibrary):
            resolved = await core_style.resolve_reference_values(
                self.client, [f"asset-{n}" for n in range(6)] + ["https://cdn.example/x.mp3"]
            )
        self.assertEqual(len(created), 1)
        self.assertEqual([item["uri"] for item in resolved],
                         [f"asset://asset-{n}" for n in range(6)] + ["https://cdn.example/x.mp3"])
        self.assertEqual([item["kind"] for item in resolved], ["image"] * 6 + ["audio"])

    async def test_link_kind_falls_back_to_ranged_get(self):
        calls = []

        class Response:
            def __init__(self, status, content_type):
                self.status, self.headers = status, {"Content-Type": content_type}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class Session:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def head(self, url, **kwargs):
                calls.append(("HEAD", kwargs))
                return Response(403, "application/xml")  # presigned GET-only URL

            def get(self, url, **kwargs):
                calls.append(("GET", kwargs))
                return Response(206, "video/mp4")

        with mock.patch.object(core_style.aiohttp, "ClientSession", Session):
            kind = await core_style._probe_url_kind("https://bucket.example/obj?X-Signature=abc")
        self.assertEqual(kind, "video")
        self.assertEqual([method for method, _ in calls], ["HEAD", "GET"])
        self.assertEqual(calls[1][1]["headers"], {"Range": "bytes=0-0"})

    async def test_video_size_check_never_encodes(self):
        import tempfile

        class Video:
            def __init__(self, source, trim=(0, 0)):
                self.source, self.trim = source, trim

            def get_active_trim_window(self):
                return self.trim

            def get_stream_source(self):
                return self.source

            def save_to(self, *args, **kwargs):
                raise AssertionError("the size check must not re-encode the video")

        with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
            tmp.write(b"x" * 1234)
            tmp.flush()
            size = nodes_shared.video_source_size_bytes
            self.assertEqual(size(Video(tmp.name)), 1234)
            self.assertEqual(size(Video(io.BytesIO(b"y" * 10))), 10)
            self.assertIsNone(size(Video(tmp.name, trim=(1.0, 2.0))))  # upload is a re-encode

    def test_draft_tooltips_name_the_draft_to_final_node(self):
        title = nodes_seedance2.BytePlusSeedanceDraftToFinal.define_schema().display_name
        self.assertEqual(title, "BytePlus Seedance 2.5 Draft to Final Video")
        self.assertIn(title, nodes_seedance2.SEEDANCE_MODEL_TOOLTIP)
        self.assertIn(title, nodes_seedance2.DRAFT_TASK_ID_OUTPUT_TOOLTIP)

    async def test_reference_totals_follow_byteplus(self):
        """BytePlus: 50 references on 2.5 (30 + 10 + 10), 15 on 2.0 (9 + 3 + 3)."""

        def links(images, videos, audios):
            values = (
                [f"https://cdn.example/i{n}.png" for n in range(images)]
                + [f"https://cdn.example/v{n}.mp4" for n in range(videos)]
                + [f"https://cdn.example/a{n}.mp3" for n in range(audios)]
            )
            return {f"asset_{slot}": value for slot, value in enumerate(values, 1)}

        for label, caps in (("Seedance 2.5", (30, 10, 10)), ("Seedance 2.0", (9, 3, 3))):
            with self.subTest(label=label):
                nodes_video.NON_BLOCKING_TASK_CACHE.clear()
                await self.run_node(self.REF, model=self.model(label, reference_assets=links(*caps)), seed=1)
                content = self.submitted[-1]["content"]
                kinds = [item["type"] for item in content if item["type"] != "text"]
                self.assertEqual(
                    (kinds.count("image_url"), kinds.count("video_url"), kinds.count("audio_url")), caps
                )
                self.assertEqual(len(kinds), 50 if label == "Seedance 2.5" else 15)
                for index in range(3):
                    over = list(caps)
                    over[index] += 1
                    with self.assertRaisesRegex(Exception, "Too many reference"):
                        await self.run_node(self.REF, model=self.model(label, reference_assets=links(*over)), seed=1)

    async def test_content_order_labels_and_asset_resolution(self):
        self.client.asset_credentials = {"access_key": "AK", "secret_key": "SK"}
        self.asset_types = {"asset-img": ("Image", "Active"), "asset-vid": ("Video", "Active"),
                            "asset-aud": ("Audio", "Active")}
        model = self.model(
            prompt="asset1 and ASSET 3 talk over asset2; keep asset 4 and asset9",
            reference_images={"image_2": image(640, 640, 0.2), "image_1": image(640, 640, 0.8)},
            reference_audios={"audio_1": sine_audio(3)},
            reference_assets={"asset_2": "asset://asset-aud", "asset_1": "asset-img",
                              "asset_3": "asset-vid", "asset_4": "https://cdn.example/pose.PNG"},
        )
        await self.run_node(self.REF, model=model, seed=7)
        request = self.submitted[0]
        kinds = [(item["type"], item.get("role")) for item in request["content"]]
        self.assertEqual(
            kinds,
            [
                ("text", None),
                ("image_url", "reference_image"),
                ("image_url", "reference_image"),
                ("audio_url", "reference_audio"),
                ("image_url", "reference_image"),
                ("image_url", "reference_image"),
                ("video_url", "reference_video"),
                ("audio_url", "reference_audio"),
            ],
        )
        urls = [item[item["type"]]["url"] for item in request["content"][4:]]
        self.assertEqual(
            urls, ["asset://asset-img", "https://cdn.example/pose.PNG", "asset://asset-vid", "asset://asset-aud"]
        )
        self.assertEqual(
            request["content"][0]["text"],
            "Image 3 and Video 1 talk over Audio 2; keep Image 4 and asset9",
        )
        self.assertTrue(request["content"][3]["audio_url"]["url"].startswith("data:audio/wav;base64,"))
        # Slot order decides the image order: image_1 (bright) first.
        first = request["content"][1]["image_url"]["url"]
        self.assertTrue(first.startswith("data:image/jpeg;base64,"))
        self.assertEqual([c[1]["Id"] for c in self.get_asset_calls], ["asset-img", "asset-aud", "asset-vid"])
        self.assertEqual(request["seed"], 7)

    async def test_asset_ids_need_credentials_links_do_not(self):
        with self.assertRaises(Exception) as ctx:
            await self.run_node(self.REF, model=self.model(reference_assets={"asset_1": "asset-img"}), seed=1)
        self.assertIn("accessKey", str(ctx.exception))
        await self.run_node(
            self.REF, model=self.model(reference_assets={"asset_1": "https://cdn.example/clip.mp4"}), seed=1
        )
        self.assertEqual(
            self.submitted[0]["content"][1],
            {"type": "video_url", "video_url": {"url": "https://cdn.example/clip.mp4"}, "role": "reference_video"},
        )
        self.assertEqual(self.get_asset_calls, [])

    async def test_inactive_asset_is_rejected(self):
        self.client.asset_credentials = {"access_key": "AK", "secret_key": "SK"}
        self.asset_types = {"asset-img": ("Image", "Processing")}
        with self.assertRaises(Exception) as ctx:
            await self.run_node(self.REF, model=self.model(reference_assets={"asset_1": "asset-img"}), seed=1)
        self.assertIn("not Active", str(ctx.exception))

    async def test_reference_requirements_and_counts(self):
        with self.assertRaises(Exception) as ctx:
            await self.run_node(self.REF, model=self.model(), seed=1)
        self.assertIn("At least one reference", str(ctx.exception))
        # 2.5 family: audio alone is enough; 2.0: it is not.
        await self.run_node(self.REF, model=self.model(reference_audios={"audio_1": sine_audio(3)}), seed=1)
        with self.assertRaises(Exception) as ctx:
            await self.run_node(
                self.REF, model=self.model("Seedance 2.0", reference_audios={"audio_1": sine_audio(3)}), seed=1
            )
        self.assertIn("At least one reference", str(ctx.exception))
        images = {f"image_{i}": image(320, 320) for i in range(1, 10)}
        with self.assertRaises(Exception) as ctx:
            await self.run_node(
                self.REF,
                model=self.model("Seedance 2.0 Fast", reference_images=images,
                                 reference_assets={"asset_1": "https://cdn.example/extra.jpg"}),
                seed=1,
            )
        self.assertIn("Too many reference images: 10", str(ctx.exception))
        audios = {f"audio_{i}": sine_audio(2) for i in range(1, 4)}
        with self.assertRaises(Exception) as ctx:
            await self.run_node(
                self.REF,
                model=self.model("Seedance 2.0", reference_images={"image_1": image(320, 320)}, reference_audios=audios,
                                 reference_assets={"asset_1": "https://cdn.example/a.mp3"}),
                seed=1,
            )
        self.assertIn("Too many reference audios: 4", str(ctx.exception))

    async def test_task_types(self):
        with self.assertRaises(Exception) as ctx:
            await self.run_node(
                self.REF, model=self.model(task_type="edit", reference_images={"image_1": image(320, 320)}), seed=1
            )
        self.assertIn("'edit' task needs at least one reference video", str(ctx.exception))
        await self.run_node(
            self.REF,
            model=self.model(task_type="edit", reference_assets={"asset_1": "https://cdn.example/src.mov"}),
            seed=1,
        )
        request = self.submitted[-1]
        self.assertEqual((request["ratio"], request["duration"], request["omni_reference_task_type"]), ("adaptive", -1, "edit"))
        nodes_video.NON_BLOCKING_TASK_CACHE.clear()
        await self.run_node(
            self.REF, model=self.model(task_type="extend", duration=8, reference_videos={"video_1": FakeVideo()}), seed=1
        )
        request = self.submitted[-1]
        self.assertEqual((request["ratio"], request["duration"], request["omni_reference_task_type"]), ("adaptive", 8, "extend"))
        nodes_video.NON_BLOCKING_TASK_CACHE.clear()
        await self.run_node(
            self.REF, model=self.model("Seedance 2.5 Draft", resolution="480p", task_type="reference",
                                       reference_videos={"video_1": FakeVideo()}), seed=1
        )
        request = self.submitted[-1]
        self.assertEqual(request["omni_reference_task_type"], "reference")
        self.assertTrue(request["draft"])
        nodes_video.NON_BLOCKING_TASK_CACHE.clear()
        await self.run_node(
            self.REF, model=self.model("Seedance 2.0", ratio="adaptive", reference_videos={"video_1": FakeVideo(854, 480)}),
            seed=1,
        )
        request = self.submitted[-1]
        self.assertNotIn("omni_reference_task_type", request)
        self.assertEqual(request["ratio"], "adaptive")

    async def test_local_videos_are_uploaded_to_comfy_storage(self):
        await self.run_node(
            self.REF,
            model=self.model(reference_videos={"video_2": FakeVideo(1280, 720, 3), "video_1": FakeVideo(1920, 1080, 4)}),
            seed=1,
        )
        videos = [item for item in self.submitted[0]["content"] if item["type"] == "video_url"]
        self.assertEqual(
            [v["video_url"]["url"] for v in videos],
            ["https://storage.example/ref-1.mp4", "https://storage.example/ref-2.mp4"],
        )
        self.assertEqual(self.uploads[0][0].get_dimensions(), (1920, 1080))
        self.assertEqual(self.uploads[0][1]["failed_key"], "err_comfy_upload_failed_reference")

    async def test_reference_video_limits_and_scaling(self):
        scaled = []

        def fake_scale(video, width, height):
            scaled.append((video.get_dimensions(), (width, height)))
            return FakeVideo(width, height, video.duration, video.fps)

        with mock.patch.object(nodes_seedance2, "scale_video", fake_scale):
            # 2.0 at 720p allows at most 927,408 pixels: 1080p input is downscaled.
            await self.run_node(
                self.REF, model=self.model("Seedance 2.0", reference_videos={"video_1": FakeVideo(1920, 1080)}), seed=1
            )
            self.assertEqual(scaled[-1][1], (1280, 720))
            self.assertLessEqual(1280 * 720, 927_408)
            nodes_video.NON_BLOCKING_TASK_CACHE.clear()
            # Without auto_downscale only the documented range applies: 1080p passes...
            await self.run_node(
                self.REF,
                model=self.model("Seedance 2.0", auto_downscale=False, reference_videos={"video_1": FakeVideo(1920, 1080)}),
                seed=1,
            )
            nodes_video.NON_BLOCKING_TASK_CACHE.clear()
            # ...and anything above 8,295,044 pixels is rejected.
            with self.assertRaises(Exception) as ctx:
                await self.run_node(
                    self.REF,
                    model=self.model("Seedance 2.0", auto_downscale=False, reference_videos={"video_1": FakeVideo(4096, 2160)}),
                    seed=1,
                )
            self.assertIn("too large", str(ctx.exception))
            with self.assertRaises(Exception) as ctx:
                await self.run_node(
                    self.REF, model=self.model(reference_videos={"video_1": FakeVideo(640, 480)}), seed=1
                )
            self.assertIn("too small", str(ctx.exception))
            await self.run_node(
                self.REF, model=self.model(auto_upscale=True, reference_videos={"video_1": FakeVideo(640, 480)}), seed=1
            )
            width, height = scaled[-1][1]
            self.assertGreaterEqual(width * height, 407_696)
            self.assertEqual((width % 2, height % 2), (0, 0))
        cases = [
            (self.model(reference_videos={"video_1": FakeVideo(duration=1.5)}), "too short"),
            (self.model("Seedance 2.0", reference_videos={"video_1": FakeVideo(854, 480, 8), "video_2": FakeVideo(854, 480, 8)}),
             "Total reference video duration"),
            (self.model(reference_videos={"video_1": FakeVideo(fps=20)}), "frame rate"),
            (self.model(reference_videos={"video_1": FakeVideo(200, 2000)}), "width/height"),
            (self.model(task_type="edit", reference_videos={"video_1": FakeVideo(duration=3)}), "at least 4.0 seconds"),
            (self.model(reference_audios={"audio_1": sine_audio(1)}), "audio duration"),
            (self.model("Seedance 2.0", reference_images={"image_1": image(320, 320)},
                        reference_audios={"audio_1": sine_audio(8), "audio_2": sine_audio(8)}),
             "Total reference audio duration"),
        ]
        for model, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(Exception) as ctx:
                    await self.run_node(self.REF, model=model, seed=1)
                self.assertIn(message, str(ctx.exception))

    async def test_reference_media_minimum_is_two_seconds(self):
        """BytePlus: reference videos and audios must be at least 2 s long."""
        self.assertEqual(nodes_seedance2.REF_MEDIA_MIN_DURATION, 2.0)
        for label in ("Seedance 2.5", "Seedance 2.0"):
            with self.subTest(model=label):
                with self.assertRaises(Exception) as ctx:
                    await self.run_node(
                        self.REF, model=self.model(label, reference_videos={"video_1": FakeVideo(duration=1.9)}), seed=1
                    )
                self.assertIn(
                    "Reference video 1 is too short: 1.9s. Minimum duration is 2.0 seconds.", str(ctx.exception)
                )
                with self.assertRaises(Exception) as ctx:
                    await self.run_node(
                        self.REF,
                        model=self.model(label, reference_images={"image_1": image(640, 640)},
                                         reference_audios={"audio_1": sine_audio(1.9)}),
                        seed=1,
                    )
                self.assertIn("audio duration must be between 2.0s", str(ctx.exception))
                self.assertEqual(self.submitted, [])
        # Exactly 2 s is accepted.
        await self.run_node(self.REF, model=self.model(reference_videos={"video_1": FakeVideo(duration=2.0)}), seed=1)
        nodes_video.NON_BLOCKING_TASK_CACHE.clear()
        await self.run_node(
            self.REF,
            model=self.model(reference_images={"image_1": image(640, 640)}, reference_audios={"audio_1": sine_audio(2.0)}),
            seed=1,
        )
        self.assertEqual(len(self.submitted), 2)
        self.assertEqual(self.submitted[0]["model"], "dreamina-seedance-2-5-260628")

    def test_pixel_limit_table(self):
        limits = nodes_seedance2.ref_video_pixel_limits
        # BytePlus documents one range for every model and resolution; core's
        # per-resolution budgets are only auto_downscale targets.
        documented = {"min": 407_696, "max": 8_295_044}
        self.assertEqual(limits("dreamina-seedance-2-0", "720p"), {**documented, "max_target": 927_408})
        self.assertEqual(limits("dreamina-seedance-2-0", "1080p")["max_target"], 2_073_600)
        self.assertEqual(limits("dreamina-seedance-2-5", "1080p"), {**documented, "max_target": 8_295_044})
        self.assertEqual(limits("dreamina-seedance-2-5-premium", "4k"), {**documented, "max_target": 8_295_044})
        self.assertEqual(limits("dreamina-seedance-2-0", "4k"), {**documented, "max_target": 8_295_044})

    def test_label_and_rewrite_helpers(self):
        labels = nodes_seedance2.build_asset_labels(
            [(1, "image", "asset://a"), (2, "video", "asset://v"), (3, "image", "asset://a")], 2, 0, 0
        )
        self.assertEqual(labels, {1: "Image 3", 2: "Video 1", 3: "Image 3"})
        self.assertEqual(
            nodes_seedance2.rewrite_asset_refs("asset1, Asset 2, assets3, asset_1", labels),
            "Image 3, Video 1, assets3, asset_1",
        )

    def test_scale_video_with_pyav(self):
        import av
        import numpy
        from comfy_api.latest import InputImpl

        buffer = io.BytesIO()
        with av.open(buffer, mode="w", format="mp4") as container:
            stream = container.add_stream("h264", rate=24)
            stream.width, stream.height, stream.pix_fmt = 320, 240, "yuv420p"
            audio = container.add_stream("aac", rate=16000)
            audio.layout = "mono"
            for i in range(24):
                frame = av.VideoFrame.from_ndarray(
                    numpy.full((240, 320, 3), i * 8, dtype=numpy.uint8), format="rgb24"
                )
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
            samples = numpy.zeros((1, 16000), dtype=numpy.float32)
            audio_frame = av.AudioFrame.from_ndarray(samples, format="fltp", layout="mono")
            audio_frame.sample_rate = 16000
            for packet in audio.encode(audio_frame):
                container.mux(packet)
            for packet in audio.encode():
                container.mux(packet)
        buffer.seek(0)
        video = InputImpl.VideoFromFile(buffer)
        scaled = nodes_seedance2.scale_video(video, 160, 120)
        self.assertEqual(scaled.get_dimensions(), (160, 120))
        self.assertAlmostEqual(float(scaled.get_frame_rate()), 24.0, places=3)
        self.assertAlmostEqual(scaled.get_duration(), 1.0, delta=0.2)
        self.assertEqual(video.get_dimensions(), (320, 240))  # the source is still readable


@requires_comfyui
class DraftToFinalTests(_ExecutorHarness):
    def setUp(self):
        super().setUp()
        self.D2F = nodes_seedance2.BytePlusSeedanceDraftToFinal

    def draft(self, model_id, task_id="cgt-draft-1", **overrides):
        values = dict(id=task_id, model=model_id, status="succeeded", draft=True, duration=6, output_format="mp4")
        values.update(overrides)
        self.task_lookups[task_id] = SimpleNamespace(**values)
        return self.task_lookups[task_id]

    def test_model_key_mapping(self):
        mapping = nodes_seedance2.video_model_key_for_id
        self.assertEqual(mapping("dreamina-seedance-2-5-260628"), "dreamina-seedance-2-5")
        self.assertEqual(mapping("dreamina-seedance-2-5-premium-260915"), "dreamina-seedance-2-5-premium")
        self.assertEqual(mapping("dreamina-seedance-2-5-premium-261201"), "dreamina-seedance-2-5-premium")
        # Seedance 1.5 Pro is deprecated by BytePlus: no longer a known model.
        self.assertIsNone(mapping("seedance-1-5-pro-251215"))
        self.assertIsNone(nodes_seedance2.draft_final_plan("seedance-1-5-pro"))
        self.assertEqual(mapping("seedance-1-0-pro-fast-251015"), "seedance-1-0-pro-fast")
        self.assertIsNone(mapping("ep-20260101-xyz"))

    async def test_seedance25_draft_renders_1080p(self):
        self.draft("dreamina-seedance-2-5-260628")
        result = await self.run_node(self.D2F, draft_task_id=" cgt-draft-1 \n", watermark=True)
        request = self.submitted[0]
        self.assertEqual(request["model"], "dreamina-seedance-2-5-260628")
        self.assertEqual(request["content"], [{"type": "draft_task", "draft_task": {"id": "cgt-draft-1"}}])
        self.assertEqual((request["resolution"], request["watermark"]), ("1080p", True))
        for reused in ("ratio", "duration", "seed", "generate_audio", "draft", "service_tier", "output_format"):
            self.assertNotIn(reused, request)
        self.assertEqual(len(result.args), 3)

    async def test_premium_draft_renders_4k_and_keeps_mov(self):
        self.draft("dreamina-seedance-2-5-premium-260915", output_format="mov")
        await self.run_node(self.D2F, draft_task_id="cgt-draft-1", watermark=False)
        request = self.submitted[0]
        self.assertEqual(request["model"], "dreamina-seedance-2-5-premium-260915")
        self.assertEqual((request["resolution"], request["output_format"]), ("4k", "mov"))

    async def test_several_drafts_render_one_final_each(self):
        self.draft("dreamina-seedance-2-5-260628", "cgt-a")
        self.draft("dreamina-seedance-2-5-260628", "cgt-b")
        await self.run_node(self.D2F, draft_task_id="cgt-a\ncgt-b", watermark=False, generation_count=5)
        # Tasks are submitted in parallel threads, so their order is not fixed.
        self.assertCountEqual(
            [r["content"] for r in self.submitted],
            [[{"type": "draft_task", "draft_task": {"id": "cgt-a"}}], [{"type": "draft_task", "draft_task": {"id": "cgt-b"}}]],
        )

    async def test_errors(self):
        self.draft("dreamina-seedance-2-5-260628", "cgt-ok")
        self.draft("dreamina-seedance-2-5-260628", "cgt-normal", draft=False)
        self.draft("dreamina-seedance-2-5-260628", "cgt-running", status="running")
        self.draft("dreamina-seedance-2-5-260628", "cgt-failed", status="failed")
        self.draft("dreamina-seedance-2-0-260128", "cgt-20")
        self.draft("seedance-1-5-pro-251215", "cgt-15")
        self.draft("dreamina-seedance-2-5-premium-260915", "cgt-premium")
        self.task_lookups["cgt-missing"] = RuntimeError("ResourceNotFound: task not found")
        # `draft` is only documented for 1.5 Pro: a task without it is still checked.
        final = self.draft("dreamina-seedance-2-5-260628", "cgt-final")
        del final.draft
        final.draft_task_id = "cgt-ok"
        final.resolution = "1080p"
        not_480p = self.draft("dreamina-seedance-2-5-260628", "cgt-720p")
        del not_480p.draft
        not_480p.resolution = "720p"
        old = self.draft("dreamina-seedance-2-5-260628", "cgt-old")
        old.created_at = time.time() - 8 * 24 * 3600
        cases = [
            ("cgt-final", "is a final video rendered from draft cgt-ok"),
            ("cgt-720p", "is not a draft"),
            ("cgt-old", "more than 7 days old"),
            ("", "Enter a draft task ID"),
            ("cgt-normal", "is not a draft"),
            ("cgt-running", "is running"),
            ("cgt-failed", "cannot be rendered"),
            ("cgt-20", "dreamina-seedance-2-0-260128"),
            ("cgt-ok, cgt-premium", "different models"),
            # Seedance 1.5 Pro drafts are no longer rendered (model deprecated by BytePlus).
            ("cgt-15", "made with seedance-1-5-pro-251215, which cannot render a final from a draft. "
             "Supported: Seedance 2.5 and Seedance 2.5 Premium drafts."),
            ("cgt-ok, cgt-15", "seedance-1-5-pro-251215"),
            ("cgt-missing", "Could not look up draft task cgt-missing"),
        ]
        for draft_task_id, message in cases:
            with self.subTest(draft_task_id=draft_task_id):
                with self.assertRaises(Exception) as ctx:
                    await self.run_node(self.D2F, draft_task_id=draft_task_id, watermark=False)
                self.assertIn(message, str(ctx.exception))
        self.assertEqual(self.submitted, [])


# --------------------------------------------------------------------------
# Create Image / Video / Audio Asset
# --------------------------------------------------------------------------

@requires_comfyui
class CreateAssetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = []
        self.uploads = []
        self.groups = []
        self.statuses = ["Processing", "Active"]
        test = self

        class FakeLibrary:
            def __init__(self, client):
                nodes_assets.resolve_asset_credentials(client)
                self.region = getattr(client, "region", "ap-southeast-1")
                self.account_fingerprint = "acct-1"

            def call(self, action, body):
                test.calls.append((action, body))
                if action == "ListAssetGroups":
                    return {"Items": list(test.groups)}
                if action == "CreateAssetGroup":
                    return {"Id": "group-new"}
                if action == "CreateAsset":
                    return {"Id": f"asset-{[a for a, _ in test.calls].count('CreateAsset')}"}
                if action == "GetAsset":
                    status = test.statuses.pop(0) if len(test.statuses) > 1 else test.statuses[0]
                    return {"Id": body["Id"], "Status": status, "GroupId": body.get("GroupId"), "AssetType": "Image"}
                raise AssertionError(action)

        async def fake_image_upload(_cls, _image):
            test.uploads.append("image")
            return "https://storage.example/portrait.png"

        async def fake_video_upload(_cls, _video):
            test.uploads.append("video")
            return "https://storage.example/clip.mp4"

        async def fake_audio_upload(_cls, wav):
            test.uploads.append(("audio", wav[:4]))
            return "https://storage.example/voice.wav"

        self._patches = [
            mock.patch.object(nodes_assets, "AssetLibrary", FakeLibrary),
            mock.patch.object(nodes_assets, "upload_image_to_comfy_storage", fake_image_upload),
            mock.patch.object(nodes_assets, "upload_asset_video", fake_video_upload),
            mock.patch.object(nodes_assets, "upload_asset_audio", fake_audio_upload),
            mock.patch.object(nodes_assets, "ASSET_POLL_SECONDS", 0),
            mock.patch.dict(os.environ, {}),
        ]
        for patcher in self._patches:
            patcher.start()
        for key in ASSET_ENV_KEYS:
            os.environ.pop(key, None)
        nodes_assets.ASSET_UPLOAD_CACHE.clear()
        self.client = SimpleNamespace(
            region="ap-southeast-1", asset_credentials={"access_key": "AK", "secret_key": "SK", "session_token": ""}
        )
        self._hidden = {}

    def tearDown(self):
        for patcher in reversed(self._patches):
            patcher.stop()
        nodes_assets.ASSET_UPLOAD_CACHE.clear()
        for node_cls, old in self._hidden.items():
            if old is None:
                delattr(node_cls, "hidden")
            else:
                node_cls.hidden = old

    async def create(self, node_cls, **inputs):
        if node_cls not in self._hidden:
            self._hidden[node_cls] = node_cls.__dict__.get("hidden")
        node_cls.hidden = SimpleNamespace(unique_id="7")
        return await node_cls.execute(self.client, **inputs)

    def actions(self):
        return [action for action, _ in self.calls]

    def created(self):
        return next(body for action, body in self.calls if action == "CreateAsset")

    async def test_empty_group_id_finds_or_creates_aigc_group(self):
        result = await self.create(
            nodes_assets.BytePlusCreateImageAsset, image=image(640, 640), group_name="Neon Demo", asset_name="Neon"
        )
        self.assertEqual(self.actions(), ["ListAssetGroups", "CreateAssetGroup", "CreateAsset", "GetAsset", "GetAsset"])
        self.assertEqual(self.calls[0][1]["Filter"], {"Name": "Neon Demo", "GroupType": "AIGC"})
        self.assertEqual(self.calls[1][1]["GroupType"], "AIGC")
        self.assertEqual(
            {k: self.created()[k] for k in ("GroupId", "URL", "AssetType", "Name", "ProjectName")},
            {"GroupId": "group-new", "URL": "https://storage.example/portrait.png", "AssetType": "Image",
             "Name": "Neon", "ProjectName": "default"},
        )
        asset_id, group_id, asset_uri, info = result.args
        self.assertEqual((asset_id, group_id, asset_uri), ("asset-1", "group-new", "asset://asset-1"))
        self.assertEqual(json.loads(info)["status"], "Active")
        # An existing group with that name is reused.
        self.calls.clear()
        self.groups = [{"Id": "group-7", "Name": "Neon Demo"}]
        await self.create(nodes_assets.BytePlusCreateImageAsset, image=image(640, 640, 0.1), group_name="Neon Demo")
        self.assertNotIn("CreateAssetGroup", self.actions())
        self.assertEqual(self.created()["GroupId"], "group-7")

    async def test_existing_group_id_skips_group_lookup(self):
        result = await self.create(
            nodes_assets.BytePlusCreateImageAsset, image=image(640, 640), group_id=" group-liveness-1 ",
            project_name="team-a",
        )
        self.assertEqual(self.actions()[0], "CreateAsset")
        self.assertEqual(self.created()["GroupId"], "group-liveness-1")
        self.assertEqual(self.created()["ProjectName"], "team-a")
        self.assertEqual(result.args[1], "group-liveness-1")

    async def test_url_goes_straight_to_create_asset(self):
        await self.create(
            nodes_assets.BytePlusCreateImageAsset, image_url=" https://cdn.example/portrait.jpg ", group_id="g1",
            wait_until_active=False,
        )
        self.assertEqual(self.uploads, [])
        self.assertEqual(self.created()["URL"], "https://cdn.example/portrait.jpg")
        self.assertNotIn("GetAsset", self.actions())

    async def test_source_rules(self):
        cases = [
            (nodes_assets.BytePlusCreateImageAsset, {}, "Connect the image input or set image_url"),
            (nodes_assets.BytePlusCreateImageAsset, {"image": image(640, 640), "image_url": "https://x/a.png"}, "not both"),
            (nodes_assets.BytePlusCreateImageAsset, {"image_url": "http://x/a.png"}, "HTTPS"),
            (nodes_assets.BytePlusCreateImageAsset, {"image_url": "https://x/a.mp4"}, "needs image"),
            (nodes_assets.BytePlusCreateVideoAsset, {}, "Connect the video input or set video_url"),
            (nodes_assets.BytePlusCreateVideoAsset, {"video": FakeVideo(), "video_url": "https://x/a.mp4"}, "not both"),
            (nodes_assets.BytePlusCreateVideoAsset, {"video_url": "https://x/a.png"}, "needs video"),
            (nodes_assets.BytePlusCreateAudioAsset, {}, "Connect the audio input or set audio_url"),
            (nodes_assets.BytePlusCreateAudioAsset, {"audio": sine_audio(3), "audio_url": "https://x/a.mp3"}, "not both"),
            (nodes_assets.BytePlusCreateAudioAsset, {"audio_url": "https://x/a.ogg"}, ".wav or .mp3"),
            (nodes_assets.BytePlusCreateAudioAsset, {"audio_url": "https://x/a.mp4"}, "needs audio"),
        ]
        for node_cls, inputs, message in cases:
            with self.subTest(node=node_cls.NODE_ID, inputs=list(inputs)):
                with self.assertRaises(Exception) as ctx:
                    await self.create(node_cls, group_id="g1", **inputs)
                self.assertIn(message, str(ctx.exception))
        self.assertEqual(self.calls, [])

    async def test_image_validations(self):
        for img, message in (
            (image(299, 600), "between 300 and 6000"),
            (image(600, 6001), "between 300 and 6000"),
            (image(1000, 400), "aspect ratio"),  # 2.5 is excluded, as in core
            (image(400, 1000), "aspect ratio"),
        ):
            with self.subTest(size=tuple(img.shape)):
                with self.assertRaises(Exception) as ctx:
                    await self.create(nodes_assets.BytePlusCreateImageAsset, image=img, group_id="g1")
                self.assertIn(message, str(ctx.exception))
        self.assertEqual(self.calls, [])

    async def test_dedupe_cache_reuses_asset(self):
        first = await self.create(nodes_assets.BytePlusCreateImageAsset, image=image(640, 640), group_id="g1")
        second = await self.create(nodes_assets.BytePlusCreateImageAsset, image=image(640, 640), group_id="g1")
        self.assertEqual(first.args[0], second.args[0])
        self.assertEqual(self.actions().count("CreateAsset"), 1)
        self.assertEqual(self.uploads, ["image"])
        await self.create(nodes_assets.BytePlusCreateImageAsset, image=image(640, 640), group_id="g1", asset_name="other")
        self.assertEqual(self.actions().count("CreateAsset"), 2)

    async def test_video_asset(self):
        for video, message in (
            (FakeVideo(duration=1.5), "2 to 30 seconds"),
            (FakeVideo(duration=31), "2 to 30 seconds"),
            (FakeVideo(4096, 2160), "total pixels"),
            (FakeVideo(640, 480), "total pixels"),
            (FakeVideo(1300, 500), "aspect ratio"),
            (FakeVideo(fps=15), "frame rate"),
        ):
            with self.subTest(video=vars(video)):
                with self.assertRaises(Exception) as ctx:
                    await self.create(nodes_assets.BytePlusCreateVideoAsset, video=video, group_id="g1")
                self.assertIn(message, str(ctx.exception))
        self.assertEqual(self.calls, [])
        result = await self.create(nodes_assets.BytePlusCreateVideoAsset, video=FakeVideo(1280, 720, 5, 30), group_id="g1")
        self.assertEqual(self.uploads, ["video"])
        self.assertEqual((self.created()["AssetType"], self.created()["URL"]), ("Video", "https://storage.example/clip.mp4"))
        self.assertEqual(result.args[2], "asset://asset-1")
        self.calls.clear()
        await self.create(nodes_assets.BytePlusCreateVideoAsset, video_url="https://cdn.example/clip.mov", group_id="g1")
        self.assertEqual((self.created()["AssetType"], self.created()["URL"]), ("Video", "https://cdn.example/clip.mov"))
        self.assertEqual(self.uploads, ["video"])

    async def test_audio_asset(self):
        for audio, message in (
            (sine_audio(1.5), "2 to 30 seconds"),
            (sine_audio(31), "2 to 30 seconds"),
            (sine_audio(29, sample_rate=48000, channels=6), "at most 15 MB"),
        ):
            with self.subTest(seconds=audio["waveform"].shape[-1] / audio["sample_rate"]):
                with self.assertRaises(Exception) as ctx:
                    await self.create(nodes_assets.BytePlusCreateAudioAsset, audio=audio, group_id="g1")
                self.assertIn(message, str(ctx.exception))
        self.assertEqual(self.calls, [])
        result = await self.create(nodes_assets.BytePlusCreateAudioAsset, audio=sine_audio(5), group_id="g1")
        self.assertEqual(self.uploads, [("audio", b"RIFF")])
        self.assertEqual((self.created()["AssetType"], self.created()["URL"]), ("Audio", "https://storage.example/voice.wav"))
        self.assertEqual(result.args[:3], ("asset-1", "g1", "asset://asset-1"))
        await self.create(nodes_assets.BytePlusCreateAudioAsset, audio=sine_audio(5), group_id="g1")
        self.assertEqual(self.actions().count("CreateAsset"), 1)  # deduped
        self.calls.clear()
        await self.create(nodes_assets.BytePlusCreateAudioAsset, audio_url="https://cdn.example/voice.MP3", group_id="")
        self.assertEqual(self.actions()[:2], ["ListAssetGroups", "CreateAssetGroup"])
        self.assertEqual((self.created()["AssetType"], self.created()["URL"]), ("Audio", "https://cdn.example/voice.MP3"))

    async def test_missing_credentials_keep_the_legacy_hint(self):
        self.client.asset_credentials = None
        with self.assertRaises(Exception) as ctx:
            await self.create(nodes_assets.BytePlusCreateImageAsset, image_url="https://cdn.example/a.png", group_id="g1")
        self.assertIn("accessKey", str(ctx.exception))
        self.assertIn("BYTEPLUS_ACCESS_KEY", str(ctx.exception))

    async def test_upload_helpers_map_errors(self):
        import comfy.model_management as mm

        self._patches[2].stop()
        self._patches[3].stop()
        try:
            async def failing_file_upload(*_args, **_kwargs):
                raise RuntimeError("401 Unauthorized")

            async def failing_video_upload(*_args, **_kwargs):
                raise RuntimeError("401 Unauthorized")

            fake_util = types.ModuleType("comfy_api_nodes.util")
            fake_util.upload_file_to_comfyapi = failing_file_upload
            fake_util.upload_video_to_comfyapi = failing_video_upload
            with mock.patch.dict(sys.modules, {"comfy_api_nodes.util": fake_util}):
                with self.assertRaises(Exception) as ctx:
                    await nodes_assets.upload_asset_audio(object, b"RIFF")
                self.assertIn("audio_url", str(ctx.exception))
                with self.assertRaises(Exception) as ctx:
                    await nodes_assets.upload_asset_video(object, FakeVideo())
                self.assertIn("video_url", str(ctx.exception))

                async def interrupted(*_args, **_kwargs):
                    raise mm.InterruptProcessingException()

                fake_util.upload_file_to_comfyapi = interrupted
                with self.assertRaises(mm.InterruptProcessingException):
                    await nodes_assets.upload_asset_audio(object, b"RIFF")
            with mock.patch.dict(sys.modules, {"comfy_api_nodes.util": None}):
                with self.assertRaises(Exception) as ctx:
                    await nodes_assets.upload_asset_audio(object, b"RIFF")
                self.assertIn("audio_url", str(ctx.exception))
        finally:
            self._patches[2].start()
            self._patches[3].start()

    def test_comfy_file_upload_signature_matches_comfyui(self):
        import inspect

        try:
            from comfy_api_nodes.util import upload_file_to_comfyapi
        except Exception as e:  # pragma: no cover - depends on the ComfyUI checkout
            self.skipTest(f"comfy_api_nodes unavailable: {e}")
        inspect.signature(upload_file_to_comfyapi).bind(
            object, io.BytesIO(b""), "a.wav", "audio/wav", wait_label=None
        )


if __name__ == "__main__":
    unittest.main()
