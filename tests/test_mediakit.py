"""
BytePlus VOD AI MediaKit: the MediaKit Client, vCube Video Enhance (the UI
shape of ComfyUI core's ByteDanceVideoEnhanceNode, this pack's requests to
MediaKit, task polling, and the before/after comparison) and Video Smoothness
Enhance (requests, limits, pass-through without a repair, side-by-side video)
and Image Quality Enhance (requests, per-version limits, uploads, batches).

Needs a ComfyUI checkout and a Python env with torch, PyAV and the BytePlus SDK:
  COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_mediakit
"""
import asyncio
import importlib
import json
import os
import shutil
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
    import comfy.cli_args  # noqa: E402

    # Importing comfy.model_management probes CUDA unless this is set; the tests need no GPU
    # (CI installs CPU-only torch).
    comfy.cli_args.args.cpu = True

    PACKAGE_NAME = "byteplus_plugin_test"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [PLUGIN_ROOT]
        sys.modules[PACKAGE_NAME] = package

    nodes_mediakit = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_mediakit")
    nodes_video = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_video")
    from comfy_execution.graph_utils import ExecutionBlocker

STANDARD = {"tool_version": "standard", "scene": "aigc", "enhance_style": "hd"}
PROFESSIONAL = {"tool_version": "professional", "enhance_style": "natural"}
EXTRAS = ["video_url", "bitrate", "comparison", "compare_time"]
REPAIR = {"periodic_stutter": "repair", "align_source_fps": False, "insert_frame_indices": ""}
DETECT = {"periodic_stutter": "detect only"}


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

    VCUBE_RESULT = {"video_url": "https://cdn.example/enhanced.mp4?auth_key=x", "resolution": "1080p", "fps": 24}

    def __init__(self, task_statuses=("running", "completed"), submit=None, result=None):
        self.calls = []
        self.timeouts = []
        self.statuses = list(task_statuses)
        self.submit = submit or (200, {"success": True, "task_id": "amk-tool-enhance-video-1", "request_id": "r1"})
        self.result = self.VCUBE_RESULT if result is None else result

    async def __call__(self, client, method, path, body=None, timeout_seconds=None):
        self.calls.append((method, path, body))
        self.timeouts.append(timeout_seconds)
        if method == "POST":
            return self.submit
        status = self.statuses.pop(0) if self.statuses else "completed"
        if isinstance(status, tuple):  # (http_status, body)
            return status
        task = {"success": True, "task_id": "amk-tool-enhance-video-1", "status": status}
        if status == "completed":
            task["result"] = self.result
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
        # Not an output node, like core's: an unconnected node does not run (and is not billed).
        self.assertEqual(ours["output_node"], theirs["output_node"])
        self.assertFalse(ours["output_node"])
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
            with self.assertRaisesRegex(Exception, "MediaKit task t1 failed: .*InvalidVideo"):
                await nodes_mediakit.wait_for_mediakit_task(self.client, "t1", poll_seconds=0)

        # Transient query errors are retried; five in a row give up.
        flaky = FakeMediaKit(task_statuses=[(502, {"success": False}), "completed"])
        with mock.patch.object(nodes_mediakit, "_send", flaky):
            self.assertEqual((await nodes_mediakit.wait_for_mediakit_task(self.client, "t1", poll_seconds=0))["status"], "completed")
        down = FakeMediaKit(task_statuses=[(502, {"success": False})] * 5)
        with mock.patch.object(nodes_mediakit, "_send", down):
            with self.assertRaisesRegex(Exception, "HTTP 502"):
                await nodes_mediakit.wait_for_mediakit_task(self.client, "t1", poll_seconds=0)
        self.assertEqual(len(down.calls), 5)

    async def test_failed_task_and_bad_key_stop_polling(self):
        # A failed task reported with success: false is a task failure, not a retryable error.
        failed = FakeMediaKit(task_statuses=[(200, {"success": False, "status": "failed",
                                                    "error": {"code": "InvalidVideo", "message": "bad"}})])
        with mock.patch.object(nodes_mediakit, "_send", failed):
            with self.assertRaisesRegex(Exception, "MediaKit task t1 failed: .*InvalidVideo"):
                await nodes_mediakit.wait_for_mediakit_task(self.client, "t1", poll_seconds=0)
        self.assertEqual(len(failed.calls), 1)
        revoked = FakeMediaKit(task_statuses=[(401, {"success": False, "error": {"code": "Unauthorized"}})] * 5)
        with mock.patch.object(nodes_mediakit, "_send", revoked):
            with self.assertRaisesRegex(Exception, "Check the MediaKit API key"):
                await nodes_mediakit.wait_for_mediakit_task(self.client, "t1", poll_seconds=0)
        self.assertEqual(len(revoked.calls), 1)
        throttled = FakeMediaKit(task_statuses=[(429, {"success": False}), "completed"])
        with mock.patch.object(nodes_mediakit, "_send", throttled):
            await nodes_mediakit.wait_for_mediakit_task(self.client, "t1", poll_seconds=0)
        self.assertEqual(len(throttled.calls), 2)

    async def test_submit_sends_a_fresh_client_token_and_retries_with_it(self):
        # Without a token MediaKit would return an earlier task for the same video,
        # tool_version and resolution (24 h), whatever else changed.
        fake = FakeMediaKit()
        with mock.patch.object(nodes_mediakit, "_send", fake), \
                mock.patch.object(nodes_mediakit, "MEDIAKIT_POLL_SECONDS", 0):
            await nodes_mediakit.submit_and_wait(self.client, "/tools/enhance-video", {"video_url": "u"}, None, "vcube_task_submitted")
            await nodes_mediakit.submit_and_wait(self.client, "/tools/enhance-video", {"video_url": "u"}, None, "vcube_task_submitted")
        tokens = [body["client_token"] for method, _path, body in fake.calls if method == "POST"]
        self.assertEqual(len(tokens), 2)
        self.assertNotEqual(tokens[0], tokens[1])
        self.assertTrue(all(len(t) <= 64 for t in tokens))

        # A transient failure is retried with the same token, so no second task is created.
        submits = [(502, {"success": False}), (200, {"success": True, "task_id": "t9"})]

        async def flaky_submit(client, method, path, body=None, timeout_seconds=None):
            if method == "POST":
                flaky_submit.bodies.append(body)
                return submits.pop(0)
            return 200, {"success": True, "task_id": "t9", "status": "completed", "result": {}}

        flaky_submit.bodies = []
        with mock.patch.object(nodes_mediakit, "_send", flaky_submit), \
                mock.patch.object(nodes_mediakit, "sleep_interruptible", mock.AsyncMock()):
            task = await nodes_mediakit.submit_and_wait(self.client, "/tools/enhance-video", {"video_url": "u"}, None, "vcube_task_submitted")
        self.assertEqual(task["task_id"], "t9")
        self.assertEqual(len(flaky_submit.bodies), 2)
        self.assertEqual(flaky_submit.bodies[0]["client_token"], flaky_submit.bodies[1]["client_token"])

        # A bad request is not retried.
        rejected = FakeMediaKit(submit=(400, {"success": False, "error": {"code": "InvalidParameter"}}))
        with mock.patch.object(nodes_mediakit, "_send", rejected):
            with self.assertRaisesRegex(Exception, "InvalidParameter"):
                await nodes_mediakit.submit_and_wait(self.client, "/tools/enhance-video", {}, None, "vcube_task_submitted")
        self.assertEqual(len(rejected.calls), 1)

    async def test_timeout_message(self):
        async def slow(client, method, path, body=None, timeout_seconds=None):
            raise asyncio.TimeoutError()

        with mock.patch.object(nodes_mediakit, "_send", slow):
            with self.assertRaisesRegex(Exception, "did not answer within 600s.*billed"):
                await nodes_mediakit.mediakit_request(self.client, "POST", "/tools-sync/enhance-image", {},
                                                      timeout_seconds=600)


@requires_comfyui
class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_errors_hide_the_signed_query(self):
        import aiohttp

        utils_download = importlib.import_module(f"{PACKAGE_NAME}.nodes.utils_download")
        seen = {}

        async def fake_stream(session, url, path, timeout=None, retries=3):
            seen["timeout"] = timeout
            raise aiohttp.ClientResponseError(
                SimpleNamespace(real_url=url), (), status=403, message="Forbidden"
            )

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(utils_download, "_download_to_file_stream_async", fake_stream), \
                mock.patch.object(nodes_mediakit, "_temp_path", lambda prefix, ext="mp4": os.path.join(tmp, "f")):
            with self.assertRaises(Exception) as ctx:
                await nodes_mediakit._download("https://cdn.example/out.mp4?auth_key=secret", "vcube_enhanced")
        message = str(ctx.exception)
        self.assertIn("https://cdn.example/out.mp4 (HTTP 403)", message)
        self.assertNotIn("secret", message)
        # Large results: no total time limit, only a stall limit.
        self.assertIsNone(seen["timeout"].total)
        self.assertEqual(seen["timeout"].sock_read, 120)

    async def test_stream_helper_takes_a_client_timeout(self):
        import aiohttp

        utils_download = importlib.import_module(f"{PACKAGE_NAME}.nodes.utils_download")
        used = []

        class Response:
            def __init__(self):
                self.content = SimpleNamespace(read=self.read)
                self.chunks = [b"abc", b""]

            async def read(self, size):
                return self.chunks.pop(0)

            def raise_for_status(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class Session:
            def get(self, url, timeout=None):
                used.append(timeout)
                return Response()

        stall_only = aiohttp.ClientTimeout(total=None, sock_read=120)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.bin")
            self.assertTrue(await utils_download._download_to_file_stream_async(Session(), "u", path, timeout=stall_only))
            self.assertTrue(await utils_download._download_to_file_stream_async(Session(), "u", path, timeout=30))
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"abc")
        self.assertIs(used[0], stall_only)
        self.assertEqual(used[1].total, 30)


class NodeFixture(unittest.IsolatedAsyncioTestCase):
    """Synthetic source (red, 320x180, 12 fps) and result (blue, 640x360, 24 fps) clips; fake downloads."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = os.path.join(self.tmp.name, "source.mp4")
        self.enhanced = os.path.join(self.tmp.name, "enhanced.mp4")
        make_video(self.source, 320, 180, 12, 1, 0)
        make_video(self.enhanced, 640, 360, 24, 1, 2)
        self.downloads = []
        self.copies = []
        test = self
        counter = iter(range(1000))

        async def fake_download(url, prefix, ext="mp4"):
            test.downloads.append((url, prefix))
            fixture = test.enhanced if prefix in ("vcube_enhanced", "smooth_repaired") else test.source
            copy = os.path.join(test.tmp.name, f"download_{prefix}_{next(counter)}.{ext}")
            shutil.copy(fixture, copy)
            test.copies.append((prefix, copy))
            return copy

        self._patches = [
            mock.patch.object(nodes_mediakit, "_download", fake_download),
            mock.patch.object(nodes_mediakit, "MEDIAKIT_POLL_SECONDS", 0),
            # Keep test files out of the ComfyUI install's temp folder.
            mock.patch.object(nodes_mediakit, "_temp_path",
                              lambda prefix, ext="mp4": os.path.join(self.tmp.name, f"{prefix}_{next(counter)}.{ext}")),
        ]
        for patcher in self._patches:
            patcher.start()
        self.client = nodes_mediakit.MediaKitClient("test-key")

    def tearDown(self):
        for patcher in self._patches:
            patcher.stop()
        self.tmp.cleanup()


@requires_comfyui
class NodeTests(NodeFixture):
    def setUp(self):
        super().setUp()
        self.node = nodes_mediakit.BytePlusVideoEnhance
        self.node.hidden = SimpleNamespace(unique_id="7", prompt={})

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
        # The downloaded source was only needed for the comparison.
        self.assertFalse(os.path.exists(dict(self.copies)["vcube_source"]))

    async def test_comparison_failure_keeps_the_result(self):
        def broken(*args):
            raise MemoryError("8K stills")

        with mock.patch.object(nodes_mediakit, "_send", FakeMediaKit()), \
                mock.patch.object(nodes_mediakit, "build_comparison", broken):
            result = await self.run_node(video_url="https://cdn.example/source.mp4")
        enhanced, comparison, source_frame, enhanced_frame, _response = result.args
        self.assertEqual(enhanced.get_dimensions(), (640, 360))  # the paid result is not lost
        for blocked in (comparison, source_frame, enhanced_frame):
            self.assertIsInstance(blocked, ExecutionBlocker)
        self.assertFalse(os.path.exists(dict(self.copies)["vcube_source"]))

    async def test_connected_video_comparison_copy_is_removed(self):
        from comfy_api.input_impl import VideoFromFile

        async def fake_upload(cls, videos, **keys):
            return ["https://storage.example/upload.mp4"]

        with mock.patch.object(nodes_video, "upload_videos_to_comfy_storage_cached", fake_upload), \
                mock.patch.object(nodes_mediakit, "_send", FakeMediaKit()):
            result = await self.run_node(video=VideoFromFile(self.source))
        self.assertEqual(result.args[1].get_dimensions(), (640, 360))
        leftovers = [name for name in os.listdir(self.tmp.name) if name.startswith("vcube_source")]
        self.assertEqual(leftovers, [])

    async def test_bitrate_range(self):
        with mock.patch.object(nodes_mediakit, "_send", FakeMediaKit()) as fake:
            with self.assertRaisesRegex(Exception, "10-150000 kbps, got 5"):
                await self.run_node(video_url="https://cdn.example/source.mp4", bitrate=5)
        self.assertEqual(fake.calls, [])

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
        self.assertEqual(uploads[0]["failed_key"], "err_comfy_video_upload_failed_mediakit")
        # Comparison off: nodes using those outputs are skipped silently, not fed None.
        for blocked in result.args[1:4]:
            self.assertIsInstance(blocked, ExecutionBlocker)
            self.assertIsNone(blocked.message)

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
class SmoothnessRequestTests(unittest.TestCase):
    URL = "https://cdn.example/source.mp4"

    def test_schema(self):
        info = nodes_mediakit.BytePlusVideoSmoothness.GET_NODE_INFO_V1()
        self.assertEqual(info["display_name"], "BytePlus Video Smoothness Enhance")
        schema = nodes_mediakit.BytePlusVideoSmoothness.define_schema()
        self.assertEqual(
            [i.id for i in schema.inputs],
            ["mediakit_client", "video", "periodic_stutter", "duplicate_frames", "video_url", "comparison"],
        )
        optional = info["input"]["optional"]
        for name in ("video_url", "comparison"):
            self.assertTrue(optional[name][1]["advanced"], name)
        self.assertIn("video", optional)
        options = info["input"]["required"]["periodic_stutter"][1]["options"]
        self.assertEqual([o["key"] for o in options], ["repair", "detect only"])
        self.assertEqual(info["input"]["required"]["duplicate_frames"][1]["options"], ["remove", "detect only"])
        self.assertEqual(
            info["output_name"], ["VIDEO", "comparison", "inserted_frame_count", "duplicate_frame_count", "response"]
        )
        self.assertFalse(info["output_node"])  # runs only when something uses its outputs, like vCube

    def test_default_request_repairs_both(self):
        body = nodes_mediakit.build_smoothness_request(self.URL, REPAIR, "remove")
        self.assertEqual(body, {
            "video_url": self.URL,
            "periodic_stutter_detect": {"periodic_stutter_repair": True, "align_source_fps": False},
            "duplicate_frame_detect": {"duplicate_frame_repair": True},
        })
        self.assertTrue(nodes_mediakit.smoothness_repairs(body))

    def test_detect_only_and_options(self):
        body = nodes_mediakit.build_smoothness_request(self.URL, DETECT, "detect only")
        self.assertEqual(body["periodic_stutter_detect"], {"periodic_stutter_repair": False})
        self.assertEqual(body["duplicate_frame_detect"], {"duplicate_frame_repair": False})
        self.assertFalse(nodes_mediakit.smoothness_repairs(body))
        body = nodes_mediakit.build_smoothness_request(
            self.URL, {**REPAIR, "align_source_fps": True, "insert_frame_indices": "120, 80"}, "detect only"
        )
        self.assertEqual(
            body["periodic_stutter_detect"],
            {"periodic_stutter_repair": True, "align_source_fps": True, "insert_frame_indices": [80, 120]},
        )
        self.assertTrue(nodes_mediakit.smoothness_repairs(body))

    def test_frame_indices(self):
        self.assertEqual(nodes_mediakit.parse_frame_indices(" 3 1,2 "), [1, 2, 3])
        self.assertEqual(nodes_mediakit.parse_frame_indices(""), [])
        for bad in ("80, 80", "-1", "1.5", "a"):
            with self.subTest(value=bad), self.assertRaisesRegex(Exception, "insert_frame_indices"):
                nodes_mediakit.parse_frame_indices(bad)

    def test_source_limits(self):
        def video(seconds, size):
            return SimpleNamespace(get_duration=lambda: seconds, get_dimensions=lambda: size)

        nodes_mediakit.validate_smoothness_source(video(35.0, (3840, 2160)), repair=True)
        nodes_mediakit.validate_smoothness_source(video(600.0, (1280, 720)), repair=False)  # detection: no limit
        with self.assertRaisesRegex(Exception, "up to 35s"):
            nodes_mediakit.validate_smoothness_source(video(35.5, (1280, 720)), repair=True)
        with self.assertRaisesRegex(Exception, "at most 4096x2160"):
            nodes_mediakit.validate_smoothness_source(video(5.0, (4320, 2160)), repair=True)


@requires_comfyui
class SmoothnessNodeTests(NodeFixture):
    REPAIRED = {
        "video_url": "https://cdn.example/repaired.mp4?auth_key=x",
        "duration": 1.0,
        "inserted_frames_detail": {"inserted_frame_count": 3},
        "duplicate_frames_detail": {"duplicate_frame_count": 2},
    }

    def setUp(self):
        super().setUp()
        self.node = nodes_mediakit.BytePlusVideoSmoothness
        self.node.hidden = SimpleNamespace(unique_id="8", prompt={})

    async def run_node(self, **kwargs):
        args = dict(periodic_stutter=REPAIR, duplicate_frames="remove")
        args.update(kwargs)
        return await self.node.execute(self.client, **args)

    async def test_video_url_with_comparison(self):
        fake = FakeMediaKit(result=self.REPAIRED)
        with mock.patch.object(nodes_mediakit, "_send", fake):
            result = await self.run_node(video_url="https://cdn.example/source.mp4")
        method, path, body = fake.calls[0]
        self.assertEqual((method, path), ("POST", "/tools/enhance-video-smoothness"))
        self.assertEqual(body["video_url"], "https://cdn.example/source.mp4")
        repaired, comparison, inserted, duplicates, response = result.args
        self.assertEqual((inserted, duplicates), (3, 2))
        self.assertEqual(repaired.get_dimensions(), (640, 360))
        self.assertEqual(comparison.get_dimensions(), (1280, 360))  # two halves
        self.assertEqual(json.loads(response)["result"]["duplicate_frames_detail"], {"duplicate_frame_count": 2})
        self.assertEqual([p for _u, p in self.downloads], ["smooth_repaired", "smooth_source"])

    async def test_comparison_failure_keeps_the_result(self):
        def broken(*args):
            raise RuntimeError("decoder error")

        with mock.patch.object(nodes_mediakit, "_send", FakeMediaKit(result=self.REPAIRED)), \
                mock.patch.object(nodes_mediakit, "build_side_by_side", broken):
            result = await self.run_node(video_url="https://cdn.example/source.mp4")
        self.assertEqual(result.args[0].get_dimensions(), (640, 360))
        self.assertIsInstance(result.args[1], ExecutionBlocker)
        self.assertEqual(result.args[2:4], (3, 2))

    async def test_no_repair_passes_the_source_through(self):
        from comfy_api.input_impl import VideoFromFile

        detected = {"duration": 1.0, "inserted_frames_detail": {"inserted_frame_count": 4},
                    "duplicate_frames_detail": {"duplicate_frame_count": 0}}
        with mock.patch.object(nodes_mediakit, "_send", FakeMediaKit(result=detected)):
            result = await self.run_node(video_url="https://cdn.example/source.mp4",
                                         periodic_stutter=DETECT, duplicate_frames="detect only")
        output, comparison, inserted, duplicates, _response = result.args
        self.assertEqual(output.get_dimensions(), (320, 180))  # the downloaded source
        self.assertIsInstance(comparison, ExecutionBlocker)
        self.assertEqual((inserted, duplicates), (4, 0))
        self.assertEqual([p for _u, p in self.downloads], ["smooth_source"])

        # A connected video is returned as is: nothing to download.
        self.downloads.clear()
        source = VideoFromFile(self.source)

        async def fake_upload(cls, videos, **keys):
            return ["https://storage.example/upload.mp4"]

        with mock.patch.object(nodes_video, "upload_videos_to_comfy_storage_cached", fake_upload), \
                mock.patch.object(nodes_mediakit, "_send", FakeMediaKit(result={})):
            result = await self.run_node(video=source)
        self.assertIs(result.args[0], source)
        self.assertEqual(result.args[2:4], (0, 0))
        self.assertEqual(self.downloads, [])

    async def test_connected_video_is_uploaded(self):
        from comfy_api.input_impl import VideoFromFile

        uploads = []

        async def fake_upload(cls, videos, **keys):
            uploads.append(keys)
            return ["https://storage.example/upload.mp4"]

        fake = FakeMediaKit(result=self.REPAIRED)
        with mock.patch.object(nodes_video, "upload_videos_to_comfy_storage_cached", fake_upload), \
                mock.patch.object(nodes_mediakit, "_send", fake):
            result = await self.run_node(video=VideoFromFile(self.source), comparison=False)
        self.assertEqual(fake.calls[0][2]["video_url"], "https://storage.example/upload.mp4")
        self.assertEqual(uploads[0]["failed_key"], "err_comfy_video_upload_failed_mediakit")
        self.assertIsInstance(result.args[1], ExecutionBlocker)
        self.assertEqual([p for _u, p in self.downloads], ["smooth_repaired"])

    async def test_source_validation(self):
        from comfy_api.input_impl import VideoFromFile

        long_video = SimpleNamespace(get_duration=lambda: 40.0, get_dimensions=lambda: (1280, 720))
        with mock.patch.object(nodes_mediakit, "_send", FakeMediaKit()) as fake:
            with self.assertRaisesRegex(Exception, "not both"):
                await self.run_node(video=VideoFromFile(self.source), video_url="https://cdn.example/a.mp4")
            with self.assertRaisesRegex(Exception, "Connect a video"):
                await self.run_node()
            with self.assertRaisesRegex(Exception, "up to 35s"):
                await self.run_node(video=long_video)
            with self.assertRaisesRegex(Exception, "insert_frame_indices"):
                await self.run_node(video_url="https://cdn.example/a.mp4",
                                    periodic_stutter={**REPAIR, "insert_frame_indices": "5, 5"})
        self.assertEqual(fake.calls, [])

    def test_side_by_side(self):
        path = nodes_mediakit.build_side_by_side(self.source, self.enhanced)
        import av

        with av.open(path) as container:
            frames = [f.to_ndarray(format="rgb24") for f in container.decode(container.streams.video[0])]
        self.assertEqual(len(frames), 24)  # the repaired clip's timing (source is 12 fps)
        first = frames[0]
        self.assertEqual(first.shape, (360, 1280, 3))
        self.assertGreater(first[300, 100, 0], first[300, 100, 2])  # left: source (red)
        self.assertGreater(first[300, 1100, 2], first[300, 1100, 0])  # right: repaired (blue)
        self.assertEqual(nodes_mediakit.side_by_side_size(3840, 2160), (1920, 1080))
        self.assertEqual(nodes_mediakit.side_by_side_size(1080, 1920), (1080, 1920))
        self.assertEqual(nodes_mediakit.side_by_side_size(2560, 1080), (1920, 810))


def write_image(path, width, height, channel):
    import numpy
    from PIL import Image

    array = numpy.zeros((height, width, 3), numpy.uint8)
    array[..., channel] = 200
    Image.fromarray(array).save(path)


def solid(width, height, channel, batch=1):
    import torch

    image = torch.zeros((batch, height, width, 3))
    image[..., channel] = 0.8
    return image


@requires_comfyui
class ImageEnhanceRequestTests(unittest.TestCase):
    URL = "https://cdn.example/source.jpg"
    TWO_X = {"output_size": "multiple", "multiple": 2.0}

    def build(self, tool=None, size=None):
        return nodes_mediakit.build_image_enhance_request(
            self.URL, tool or {"tool_version": "standard"}, size or self.TWO_X
        )

    def plan(self, version, size=None, output=None, **tool):
        body = self.build({"tool_version": version, **tool}, output)
        return nodes_mediakit.plan_image_enhance(body, size)

    def test_schema(self):
        info = nodes_mediakit.BytePlusImageEnhance.GET_NODE_INFO_V1()
        self.assertEqual(info["display_name"], "BytePlus Image Quality Enhance")
        schema = nodes_mediakit.BytePlusImageEnhance.define_schema()
        self.assertEqual([i.id for i in schema.inputs],
                         ["mediakit_client", "image", "tool_version", "output_size", "image_url"])
        self.assertTrue(info["input"]["optional"]["image_url"][1]["advanced"])
        versions = info["input"]["required"]["tool_version"][1]["options"]
        self.assertEqual([o["key"] for o in versions], ["standard", "professional", "max"])
        sizes = info["input"]["required"]["output_size"][1]["options"]
        self.assertEqual([o["key"] for o in sizes], ["multiple", "target size"])
        self.assertEqual(info["output_name"], ["IMAGE", "original", "response"])

    def test_requests(self):
        self.assertEqual(self.build(), {"image_url": self.URL, "tool_version": "standard", "multiple": 2.0})
        # Options of other versions left in the DynamicCombo value are not sent.
        stale = self.build({"tool_version": "standard", "generative_enhance_mode": "fidelity_first"})
        self.assertNotIn("generative_enhance_mode", stale)
        pro = self.build({"tool_version": "professional", "generative_enhance_mode": "fidelity_first",
                          "enable_correct_color": False})
        self.assertEqual(pro["generative_enhance_mode"], "fidelity_first")
        self.assertNotIn("enable_correct_color", pro)
        best = self.build({"tool_version": "max", "generative_enhance_mode": "generative_first",
                           "enable_correct_color": False}, {"output_size": "multiple", "multiple": 2.5})
        self.assertEqual((best["enable_correct_color"], best["multiple"]), (False, 2.5))
        width_only = self.build(size={"output_size": "target size", "target_width": 1920, "target_height": 0})
        self.assertEqual(width_only, {"image_url": self.URL, "tool_version": "standard", "target_width": 1920})
        both = self.build(size={"output_size": "target size", "target_width": 1200, "target_height": 900})
        self.assertEqual((both["target_width"], both["target_height"]), (1200, 900))
        self.assertNotIn("multiple", both)

    def test_version_limits(self):
        self.assertEqual(self.plan("standard", (1000, 800)), (2000, 1600))
        with self.assertRaisesRegex(Exception, "16-1440 px on the short side"):
            self.plan("standard", (1500, 1500))
        with self.assertRaisesRegex(Exception, "would be 4320x6480"):
            self.plan("standard", (1440, 2160), {"output_size": "multiple", "multiple": 3.0})
        with self.assertRaisesRegex(Exception, "between 1 and 8 for the standard"):
            self.plan("standard", (100, 100), {"output_size": "multiple", "multiple": 9.0})
        with self.assertRaisesRegex(Exception, "at least 256 px"):
            self.plan("professional", (200, 400))
        with self.assertRaisesRegex(Exception, "at most 2048 px"):
            self.plan("professional", (300, 2100))
        self.assertEqual(self.plan("professional", (256, 341), {"output_size": "multiple", "multiple": 30.0}),
                         (7680, 10230))
        with self.assertRaisesRegex(Exception, "ratio up to 32:1"):
            self.plan("max", (66, 2178))
        with self.assertRaisesRegex(Exception, "long side of at most 10240"):
            self.plan("max", (1000, 1000), {"output_size": "multiple", "multiple": 11.0})

    def test_target_size_limits(self):
        target = lambda w=0, h=0: {"output_size": "target size", "target_width": w, "target_height": h}
        # Both sides: the largest size inside the box at the source aspect ratio.
        self.assertEqual(self.plan("professional", (400, 400), target(1200, 900)), (900, 900))
        self.assertEqual(self.plan("standard", (500, 250), target(h=1000)), (2000, 1000))
        with self.assertRaisesRegex(Exception, "target_width must be between 500 and 6144"):
            self.plan("standard", (500, 500), target(400))
        with self.assertRaisesRegex(Exception, "between 1 and 8"):
            self.plan("standard", (500, 500), target(4500))  # 9x
        with self.assertRaisesRegex(Exception, "Set target_width, target_height or both"):
            self.plan("max", (500, 500), target())
        # image_url: the source size is unknown, so only the ranges are checked.
        self.assertIsNone(self.plan("professional", None, target(1920)))
        with self.assertRaisesRegex(Exception, "between 64 and 10240"):
            self.plan("max", None, target(50))

    def test_upload_encoding(self):
        import torch

        torch.manual_seed(0)
        image = torch.rand(48, 64, 3)  # noise: the PNG is bigger than a JPEG
        data, name, mime = nodes_mediakit.encode_image_for_upload(image)
        self.assertEqual((name, mime), ("image.png", "image/png"))
        self.assertTrue(data.startswith(b"\x89PNG"))
        data, name, mime = nodes_mediakit.encode_image_for_upload(image, max_bytes=len(data) - 1)
        self.assertEqual((name, mime), ("image.jpg", "image/jpeg"))
        with self.assertRaisesRegex(Exception, "larger than 10 MB"):
            nodes_mediakit.encode_image_for_upload(image, max_bytes=10)


@requires_comfyui
class ImageEnhanceNodeTests(unittest.IsolatedAsyncioTestCase):
    RESULT = {"image_url": "https://cdn.example/out.png?auth_key=x", "image_size": 100,
              "image_format": "png", "image_width": 80, "image_height": 60}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.enhanced = os.path.join(self.tmp.name, "enhanced.png")
        self.source = os.path.join(self.tmp.name, "source.png")
        write_image(self.enhanced, 80, 60, 2)
        write_image(self.source, 40, 30, 0)
        self.downloads, self.uploads = [], []
        test = self

        async def fake_download(url, prefix, ext="mp4"):
            test.downloads.append((prefix, ext))
            copy = os.path.join(test.tmp.name, f"download_{len(test.downloads)}.{ext}")
            shutil.copy(test.enhanced if prefix == "image_enhanced" else test.source, copy)
            return copy

        async def fake_upload(cls, data, filename, mime_type, wait_label=None):
            test.uploads.append((len(data.getvalue()), filename, mime_type))
            return f"https://storage.example/{len(test.uploads)}.png"

        self._patches = [
            mock.patch.object(nodes_mediakit, "_download", fake_download),
            mock.patch("comfy_api_nodes.util.upload_file_to_comfyapi", fake_upload, create=True),
            mock.patch.dict(nodes_mediakit.IMAGE_UPLOAD_CACHE, clear=True),
        ]
        for patcher in self._patches:
            patcher.start()
        self.node = nodes_mediakit.BytePlusImageEnhance
        self.node.hidden = SimpleNamespace(unique_id="9", prompt={})
        self.client = nodes_mediakit.MediaKitClient("test-key")
        self.fake = FakeMediaKit(submit=(200, {
            "success": True, "task_id": "amk-tool-enhance-image-1", "task_type": "enhance-image",
            "request_id": "r1", "result": self.RESULT, "expires_at": 1,
        }))

    def tearDown(self):
        for patcher in self._patches:
            patcher.stop()
        self.tmp.cleanup()

    async def run_node(self, **kwargs):
        args = dict(tool_version={"tool_version": "standard"}, output_size={"output_size": "multiple", "multiple": 2.0})
        args.update(kwargs)
        with mock.patch.object(nodes_mediakit, "_send", self.fake):
            return await self.node.execute(self.client, **args)

    async def test_connected_batch(self):
        import torch

        batch = torch.cat([solid(96, 72, 0), solid(96, 72, 1)])  # max: short side at least 64 px
        tool = {"tool_version": "max", "generative_enhance_mode": "fidelity_first", "enable_correct_color": True}
        result = await self.run_node(image=batch, tool_version=tool)
        enhanced, original, response = result.args
        self.assertEqual(tuple(enhanced.shape), (2, 60, 80, 3))
        self.assertEqual(tuple(original.shape), (2, 60, 80, 3))  # resized for Compare Images
        self.assertGreater(float(original[0, 30, 40, 0]), 0.7)  # first input: red
        self.assertGreater(float(original[1, 30, 40, 1]), 0.7)  # second input: green
        self.assertEqual(len(json.loads(response)), 2)
        self.assertEqual(len(self.uploads), 2)
        self.assertEqual({c[1] for c in self.fake.calls}, {"/tools-sync/enhance-image"})
        bodies = sorted(c[2]["image_url"] for c in self.fake.calls)
        self.assertEqual(bodies, ["https://storage.example/1.png", "https://storage.example/2.png"])
        self.assertEqual(self.fake.calls[0][2]["generative_enhance_mode"], "fidelity_first")
        self.assertEqual(set(self.fake.timeouts), {nodes_mediakit.MEDIAKIT_SYNC_TIMEOUT_SECONDS})
        self.assertEqual([d for d in self.downloads if d[0] != "image_enhanced"], [])
        # The same images again reuse the uploads.
        await self.run_node(image=batch, tool_version=tool)
        self.assertEqual(len(self.uploads), 2)

    async def test_image_url(self):
        result = await self.run_node(image_url="https://cdn.example/in.jpg")
        enhanced, original, response = result.args
        self.assertEqual(self.fake.calls[0][2]["image_url"], "https://cdn.example/in.jpg")
        self.assertEqual(tuple(original.shape), tuple(enhanced.shape))
        self.assertEqual(json.loads(response)["task_id"], "amk-tool-enhance-image-1")
        self.assertEqual([d[0] for d in self.downloads], ["image_enhanced", "image_source"])
        self.assertEqual(self.uploads, [])
        # Downloaded images are deleted once loaded.
        self.assertEqual([n for n in os.listdir(self.tmp.name) if n.startswith("download_")], [])

    async def test_checks_before_any_upload(self):
        with self.assertRaisesRegex(Exception, "not both"):
            await self.run_node(image=solid(40, 30, 0), image_url="https://cdn.example/in.jpg")
        with self.assertRaisesRegex(Exception, "Connect an image"):
            await self.run_node()
        with self.assertRaisesRegex(Exception, "image_url must be a public http"):
            await self.run_node(image_url="file:///tmp/a.png")
        with self.assertRaisesRegex(Exception, "16-1440 px on the short side"):
            await self.run_node(image=solid(1500, 1500, 0))
        self.assertEqual((self.fake.calls, self.uploads), ([], []))

    async def test_failure_is_readable(self):
        self.fake.submit = (200, {"success": False, "request_id": "r2", "error": {
            "code": "AbilityError", "message": "super resolution multiple ratio should be between 0 and 8"}})
        with self.assertRaisesRegex(Exception, "AbilityError.*between 0 and 8.*r2"):
            await self.run_node(image_url="https://cdn.example/in.jpg")

    async def test_a_failed_image_cancels_the_rest_of_the_batch(self):
        import torch

        started, cancelled = [], []

        async def send(client, method, path, body=None, timeout_seconds=None):
            started.append(1)
            number = len(started)
            if number == 1:
                while len(started) < 3:  # let the other two requests start first
                    await asyncio.sleep(0.01)
                return 400, {"success": False, "error": {"code": "BadImage", "message": "nope"}}
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append(number)
                raise

        batch = torch.cat([solid(40, 30, 0), solid(40, 30, 1), solid(40, 30, 2)])
        with mock.patch.object(nodes_mediakit, "_send", send):
            with self.assertRaisesRegex(Exception, "BadImage"):
                await asyncio.wait_for(self.node.execute(
                    self.client, tool_version={"tool_version": "standard"},
                    output_size={"output_size": "multiple", "multiple": 2.0}, image=batch), 5)
        self.assertEqual(sorted(cancelled), [2, 3])

    async def test_interrupt_cancels_the_request(self):
        import comfy.model_management

        started = asyncio.Event()

        async def slow_send(client, method, path, body=None, timeout_seconds=None):
            started.set()
            await asyncio.sleep(30)

        async def interrupt_soon():
            await started.wait()
            comfy.model_management.interrupt_current_processing(True)

        try:
            with mock.patch.object(nodes_mediakit, "_send", slow_send):
                waiter = asyncio.ensure_future(interrupt_soon())
                with self.assertRaises(comfy.model_management.InterruptProcessingException):
                    await nodes_mediakit.mediakit_request(self.client, "POST", "/tools-sync/enhance-image", {})
                await waiter
        finally:
            comfy.model_management.interrupt_current_processing(False)


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
