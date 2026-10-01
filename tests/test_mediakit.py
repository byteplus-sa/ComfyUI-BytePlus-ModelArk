"""
BytePlus VOD AI MediaKit: the MediaKit Client and vCube Video Enhance (the UI
shape of ComfyUI core's ByteDanceVideoEnhanceNode, this pack's requests to
MediaKit, task polling, and the before/after comparison).

Needs a ComfyUI checkout and a Python env with torch, PyAV and the BytePlus SDK:
  COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_mediakit
"""
import importlib
import json
import os
import sys
import tempfile
import types
import unittest
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

    nodes_mediakit = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_mediakit")
    nodes_video = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_video")

STANDARD = {"tool_version": "standard", "scene": "aigc", "enhance_style": "hd"}
PROFESSIONAL = {"tool_version": "professional", "enhance_style": "natural"}
EXTRAS = ["video_url", "bitrate", "comparison", "compare_time"]


def make_video(path, width, height, fps, seconds, channel):
    import av
    import numpy

    with av.open(path, "w") as container:
        stream = container.add_stream("libx264", rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        for i in range(int(fps * seconds)):
            frame = numpy.zeros((height, width, 3), numpy.uint8)
            frame[..., channel] = 60 + (i * 5) % 150
            for packet in stream.encode(av.VideoFrame.from_ndarray(frame, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


class FakeMediaKit:
    """Replaces nodes_mediakit._send: records calls, answers from scripted responses."""

    def __init__(self, task_statuses=("running", "completed"), submit=None):
        self.calls = []
        self.statuses = list(task_statuses)
        self.submit = submit or (200, {"success": True, "task_id": "amk-tool-enhance-video-1", "request_id": "r1"})

    async def __call__(self, client, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "POST":
            return self.submit
        status = self.statuses.pop(0) if self.statuses else "completed"
        if isinstance(status, tuple):  # (http_status, body)
            return status
        task = {"success": True, "task_id": "amk-tool-enhance-video-1", "status": status}
        if status == "completed":
            task["result"] = {"video_url": "https://cdn.example/enhanced.mp4?auth_key=x", "resolution": "1080p", "fps": 24}
        if status == "failed":
            task["error"] = {"code": "InvalidVideo", "message": "decode failed"}
        return 200, task


@requires_comfyui
class SchemaTests(unittest.TestCase):
    def test_matches_core_vcube_node(self):
        try:
            core = importlib.import_module("comfy_api_nodes.nodes_bytedance")
        except Exception as e:  # pragma: no cover - depends on the ComfyUI checkout
            self.skipTest(f"core ByteDance nodes unavailable: {e}")
        ours = nodes_mediakit.BytePlusVideoEnhance.GET_NODE_INFO_V1()
        theirs = core.ByteDanceVideoEnhanceNode.GET_NODE_INFO_V1()
        self.assertEqual(ours["display_name"], "BytePlus vCube Video Enhance")
        self.assertEqual(ours["description"], theirs["description"])
        our_inputs = {**ours["input"]["required"], **ours["input"].get("optional", {})}
        core_inputs = {**theirs["input"]["required"], **theirs["input"].get("optional", {})}
        # Core's inputs in core's order, after our client; this pack's extras last.
        schema = nodes_mediakit.BytePlusVideoEnhance.define_schema()
        self.assertEqual(
            [i.id for i in schema.inputs],
            ["mediakit_client", *core_inputs, *EXTRAS],
        )
        for name, spec in core_inputs.items():
            with self.subTest(input=name):
                mine = our_inputs[name]
                self.assertEqual(mine[0], spec[0])
                a, b = dict(mine[1] if len(mine) > 1 else {}), dict(spec[1] if len(spec) > 1 else {})
                if name == "video":
                    # Ours is optional (video_url is the alternative) and names the upload.
                    for key in ("tooltip",):
                        a.pop(key, None), b.pop(key, None)
                self.assertEqual(a, b)
        self.assertIn("video", ours["input"]["optional"])
        for name in EXTRAS:
            self.assertTrue(our_inputs[name][1]["advanced"], name)
        self.assertEqual(ours["output"][:1], theirs["output"])
        self.assertEqual(ours["output_name"], ["VIDEO", "comparison", "source_frame", "enhanced_frame", "response"])

    def test_client_node(self):
        info = nodes_mediakit.BytePlusMediaKitClient.GET_NODE_INFO_V1()
        self.assertEqual(list(info["input"]["required"]), ["new_api_key", "new_key_name", "key_name", "region"])
        options = info["input"]["required"]["key_name"][1]["options"]
        self.assertEqual(options[-2:], [nodes_mediakit.ENV_KEY_OPTION, "Custom"])
        self.assertEqual(info["output"], ["BYTEPLUS_MEDIAKIT_CLIENT"])


@requires_comfyui
class RequestTests(unittest.TestCase):
    URL = "https://cdn.example/source.mp4"

    def build(self, tool=STANDARD, resolution=None, fps="source", bitrate_level="medium", bitrate=0,
              size=(1280, 720), source_fps=24.0):
        return nodes_mediakit.build_enhance_request(
            self.URL, tool, resolution or {"resolution": "1080p"}, fps, bitrate_level, bitrate, size, source_fps
        )

    def test_standard_and_professional(self):
        self.assertEqual(
            self.build(),
            {"video_url": self.URL, "tool_version": "standard", "scene": "aigc", "enhance_style": "hd",
             "resolution": "1080p", "fps": 24.0, "bitrate_level": "medium"},
        )
        pro = self.build(tool={**PROFESSIONAL, "scene": "aigc"})
        self.assertNotIn("scene", pro)  # scene only applies to the Standard version
        self.assertEqual((pro["tool_version"], pro["enhance_style"]), ("professional", "natural"))

    def test_resolution_options(self):
        self.assertEqual(self.build(resolution={"resolution": "4k"})["resolution"], "4k")
        custom = self.build(resolution={"resolution": "custom", "short_side": 1440})
        self.assertEqual(custom["resolution_limit"], 1440)
        self.assertNotIn("resolution", custom)
        self.assertEqual(self.build(resolution={"resolution": "source"}, size=(960, 540))["resolution_limit"], 540)
        # Unknown source size (video_url): keep the source size by sending nothing.
        unknown = self.build(resolution={"resolution": "source"}, size=None)
        self.assertNotIn("resolution", unknown)
        self.assertNotIn("resolution_limit", unknown)

    def test_fps_and_bitrate(self):
        self.assertEqual(self.build(source_fps=29.97)["fps"], 29.97)
        self.assertEqual(self.build(source_fps=240.0)["fps"], 120.0)
        self.assertNotIn("fps", self.build(source_fps=10.0))  # below 15: keep the source rate
        self.assertNotIn("fps", self.build(source_fps=None))
        self.assertEqual(self.build(fps="60")["fps"], 60.0)
        self.assertEqual(self.build(bitrate=8000)["bitrate"], 8000)
        self.assertNotIn("bitrate", self.build(bitrate=0))

    def test_source_video_limits(self):
        video = SimpleNamespace(get_duration=lambda: 30.0, get_dimensions=lambda: (2560, 1440))
        self.assertEqual(nodes_mediakit.validate_source_video(video), (2560, 1440))
        with self.assertRaisesRegex(Exception, "at most 2560x1440"):
            nodes_mediakit.validate_source_video(
                SimpleNamespace(get_duration=lambda: 30.0, get_dimensions=lambda: (3840, 2160))
            )
        with self.assertRaisesRegex(Exception, "at most 600s"):
            nodes_mediakit.validate_source_video(
                SimpleNamespace(get_duration=lambda: 601.0, get_dimensions=lambda: (1280, 720))
            )


@requires_comfyui
class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = nodes_mediakit.MediaKitClient("test-key")

    async def test_errors_are_readable(self):
        fake = FakeMediaKit(submit=(400, {
            "success": False, "request_id": "req-9",
            "error": {"code": "InvalidParameter", "message": "Parameter `resolution` is invalid.", "param": "resolution"},
        }))
        with mock.patch.object(nodes_mediakit, "_send", fake):
            with self.assertRaises(Exception) as ctx:
                await nodes_mediakit.mediakit_request(self.client, "POST", "/tools/enhance-video", {})
        message = str(ctx.exception)
        for part in ("InvalidParameter", "(param: resolution)", "is invalid", "req-9"):
            self.assertIn(part, message)
        fake = FakeMediaKit(submit=(401, {"success": False, "error": {"code": "Unauthorized", "message": "bad key"}}))
        with mock.patch.object(nodes_mediakit, "_send", fake):
            with self.assertRaisesRegex(Exception, "Check the MediaKit API key"):
                await nodes_mediakit.mediakit_request(self.client, "POST", "/tools/enhance-video", {})

    async def test_polling(self):
        fake = FakeMediaKit(task_statuses=["running", "running", "completed"])
        with mock.patch.object(nodes_mediakit, "_send", fake):
            task = await nodes_mediakit.wait_for_mediakit_task(self.client, "t1", poll_seconds=0)
        self.assertEqual(task["status"], "completed")
        self.assertEqual([c[:2] for c in fake.calls], [("GET", "/tasks/t1")] * 3)
        self.assertEqual(nodes_mediakit.task_result(task)["resolution"], "1080p")
        # The API page shows the result as "output" in one example.
        self.assertEqual(nodes_mediakit.task_result({"output": {"video_url": "u"}}), {"video_url": "u"})

        with mock.patch.object(nodes_mediakit, "_send", FakeMediaKit(task_statuses=["failed"])):
            with self.assertRaisesRegex(Exception, "vCube task t1 failed: .*InvalidVideo"):
                await nodes_mediakit.wait_for_mediakit_task(self.client, "t1", poll_seconds=0)

        # Transient query errors are retried; five in a row give up.
        flaky = FakeMediaKit(task_statuses=[(502, {"success": False}), "completed"])
        with mock.patch.object(nodes_mediakit, "_send", flaky):
            self.assertEqual((await nodes_mediakit.wait_for_mediakit_task(self.client, "t1", poll_seconds=0))["status"], "completed")
        down = FakeMediaKit(task_statuses=[(502, {"success": False})] * 5)
        with mock.patch.object(nodes_mediakit, "_send", down):
            with self.assertRaisesRegex(Exception, "HTTP 502"):
                await nodes_mediakit.wait_for_mediakit_task(self.client, "t1", poll_seconds=0)


@requires_comfyui
class NodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = os.path.join(self.tmp.name, "source.mp4")
        self.enhanced = os.path.join(self.tmp.name, "enhanced.mp4")
        make_video(self.source, 320, 180, 12, 1, 0)
        make_video(self.enhanced, 640, 360, 24, 1, 2)
        self.downloads = []
        test = self

        async def fake_download(url, prefix):
            test.downloads.append((url, prefix))
            return test.enhanced if "enhanced" in prefix else test.source

        counter = iter(range(1000))
        self._patches = [
            mock.patch.object(nodes_mediakit, "_download", fake_download),
            mock.patch.object(nodes_mediakit, "MEDIAKIT_POLL_SECONDS", 0),
            # Keep test files out of the ComfyUI install's temp folder.
            mock.patch.object(nodes_mediakit, "_temp_path",
                              lambda prefix, ext="mp4": os.path.join(self.tmp.name, f"{prefix}_{next(counter)}.{ext}")),
        ]
        for patcher in self._patches:
            patcher.start()
        self.node = nodes_mediakit.BytePlusVideoEnhance
        self.node.hidden = SimpleNamespace(unique_id="7", prompt={})
        self.client = nodes_mediakit.MediaKitClient("test-key")

    def tearDown(self):
        for patcher in self._patches:
            patcher.stop()
        self.tmp.cleanup()

    async def run_node(self, **kwargs):
        args = dict(tool_version=STANDARD, resolution={"resolution": "1080p"}, fps="source", bitrate_level="medium")
        args.update(kwargs)
        return await self.node.execute(self.client, **args)

    async def test_video_url_with_comparison(self):
        fake = FakeMediaKit()
        with mock.patch.object(nodes_mediakit, "_send", fake):
            result = await self.run_node(video_url="https://cdn.example/source.mp4")
        method, path, body = fake.calls[0]
        self.assertEqual((method, path), ("POST", "/tools/enhance-video"))
        self.assertEqual(body["video_url"], "https://cdn.example/source.mp4")
        self.assertNotIn("fps", body)  # source rate unknown for a link: MediaKit keeps it
        enhanced, comparison, source_frame, enhanced_frame, response = result.args
        self.assertEqual(enhanced.get_dimensions(), (640, 360))
        self.assertEqual(comparison.get_dimensions(), (640, 360))
        self.assertEqual(tuple(source_frame.shape), (1, 360, 640, 3))
        self.assertEqual(tuple(enhanced_frame.shape), (1, 360, 640, 3))
        self.assertEqual(json.loads(response)["status"], "completed")
        self.assertEqual([p for _u, p in self.downloads], ["vcube_enhanced", "vcube_source"])

    async def test_connected_video_is_uploaded(self):
        from comfy_api.input_impl import VideoFromFile

        uploads = []

        async def fake_upload(cls, videos, **keys):
            uploads.append(keys)
            return ["https://storage.example/upload.mp4"]

        fake = FakeMediaKit()
        with mock.patch.object(nodes_video, "upload_videos_to_comfy_storage_cached", fake_upload), \
                mock.patch.object(nodes_mediakit, "_send", fake):
            result = await self.run_node(video=VideoFromFile(self.source), comparison=False,
                                         resolution={"resolution": "source"}, bitrate=6000)
        body = fake.calls[0][2]
        self.assertEqual(body["video_url"], "https://storage.example/upload.mp4")
        self.assertEqual((body["resolution_limit"], body["bitrate"]), (180, 6000))
        self.assertNotIn("fps", body)  # 12 fps source: below 15, so core keeps the source rate
        self.assertEqual(uploads[0]["failed_key"], "err_comfy_video_upload_failed_vcube")
        self.assertEqual(result.args[1:4], (None, None, None))  # comparison off

    async def test_source_validation(self):
        from comfy_api.input_impl import VideoFromFile

        with mock.patch.object(nodes_mediakit, "_send", FakeMediaKit()) as fake:
            with self.assertRaisesRegex(Exception, "not both"):
                await self.run_node(video=VideoFromFile(self.source), video_url="https://cdn.example/a.mp4")
            with self.assertRaisesRegex(Exception, "Connect a video"):
                await self.run_node()
            with self.assertRaisesRegex(Exception, "public http"):
                await self.run_node(video_url="ftp://cdn.example/a.mp4")
        self.assertEqual(fake.calls, [])

    def test_comparison_frames_and_sweep(self):
        path, source_frame, enhanced_frame = nodes_mediakit.build_comparison(self.source, self.enhanced, 0.5)
        import av

        with av.open(path) as container:
            stream = container.streams.video[0]
            frames = [f.to_ndarray(format="rgb24") for f in container.decode(stream)]
        self.assertEqual(len(frames), 24)  # the enhanced clip's timing
        # Left of the divider is the source (red), right is the enhanced clip (blue).
        first = frames[0]
        self.assertGreater(first[180, 10, 0], first[180, 10, 2])
        self.assertGreater(first[180, 630, 2], first[180, 630, 0])
        self.assertEqual(tuple(source_frame.shape), tuple(enhanced_frame.shape))
        self.assertAlmostEqual(nodes_mediakit.sweep_position(0.0), 0.1)
        self.assertAlmostEqual(nodes_mediakit.sweep_position(2.0), 0.9)
        self.assertEqual(nodes_mediakit.comparison_size(3840, 2160), (1920, 1080))
        self.assertEqual(nodes_mediakit.comparison_size(640, 360), (640, 360))


@requires_comfyui
class ClientNodeTests(unittest.TestCase):
    def test_key_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = nodes_mediakit.ApiKeyStore(os.path.join(tmp, "mediakit_api_keys.json"))
            notified = []
            with mock.patch.object(nodes_mediakit, "MEDIAKIT_API_KEY_STORE", store), \
                    mock.patch.object(nodes_mediakit, "_notify_api_key_saved",
                                      lambda *a, **k: notified.append((a, k))):
                node = nodes_mediakit.BytePlusMediaKitClient
                node.hidden = SimpleNamespace(unique_id="1")
                client = node.execute("Custom", new_api_key=" mk-key ", new_key_name="work").args[0]
                self.assertEqual((client.api_key, client.base_url),
                                 ("mk-key", "https://mediakit.ap-southeast-1.bytepluses.com/api/v1"))
                self.assertEqual(notified[0][1], {"store": "mediakit"})
                self.assertEqual(node.execute("work").args[0].api_key, "mk-key")
                self.assertNotIn("mk-key", repr(client))
                with self.assertRaisesRegex(Exception, "not found"):
                    node.execute("missing")
                with self.assertRaisesRegex(Exception, "new_api_key is empty"):
                    node.execute("Custom")
                with mock.patch.dict(os.environ, {"BYTEPLUS_VOD_MEDIAKIT_API_KEY": "env-key"}):
                    self.assertEqual(node.execute(nodes_mediakit.ENV_KEY_OPTION).args[0].api_key, "env-key")
                with mock.patch.dict(os.environ, {"BYTEPLUS_VOD_MEDIAKIT_API_KEY": ""}):
                    with self.assertRaisesRegex(Exception, "is not set"):
                        node.execute(nodes_mediakit.ENV_KEY_OPTION)


if __name__ == "__main__":
    unittest.main()
