"""
Correctness fixes from the 2026-10-17 review: concurrent asset nodes, asset
polling errors, Seedance crop and frame order, MediaKit temporary files, Seed
Speech media work off the event loop, LLM turns, Draft to Final order, Video
Query Tasks bounds and the link probe resolver. Fakes only; nothing calls an API.

  COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_correctness_fixes
"""
import asyncio
import importlib
import json
import os
import sys
import threading
import time
import types
import unittest
from types import SimpleNamespace
from unittest import mock

COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
requires_comfyui = unittest.skipUnless(COMFY_ROOT, "Set COMFYUI_ROOT to a ComfyUI checkout to run these tests.")

if COMFY_ROOT:
    if COMFY_ROOT not in sys.path:
        sys.path.insert(0, COMFY_ROOT)
    import utils  # noqa: F401, E402
    import comfy.cli_args  # noqa: E402

    comfy.cli_args.args.cpu = True
    PACKAGE_NAME = "byteplus_plugin_test"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [PLUGIN_ROOT]
        sys.modules[PACKAGE_NAME] = package
    audio_utils = importlib.import_module(f"{PACKAGE_NAME}.nodes.audio_utils")
    core_style = importlib.import_module(f"{PACKAGE_NAME}.nodes.core_style")
    executor = importlib.import_module(f"{PACKAGE_NAME}.nodes.executor")
    nodes_assets = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_assets")
    nodes_seedance2 = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_seedance2")
    nodes_shared = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_shared")
    nodes_speech = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_speech")
    speech_api = importlib.import_module(f"{PACKAGE_NAME}.nodes.speech_api")
    nodes_video = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_video")
    import comfy.model_management

    Interrupt = comfy.model_management.InterruptProcessingException


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


ASSET_CLIENT = SimpleNamespace(
    region="ap-southeast-1", asset_credentials={"access_key": "AK", "secret_key": "SK", "session_token": ""}
)


class SlowAssetLibrary:
    """Asset library whose lookups take a moment, so concurrent nodes overlap; groups it creates are listed."""

    def __init__(self, state):
        self.state = state
        self.region = "ap-southeast-1"
        self.account_fingerprint = "acct-1"

    def call(self, action, body):
        state = self.state
        with state["lock"]:
            state["calls"].append(action)
        time.sleep(0.05)
        with state["lock"]:
            if action == "ListAssetGroups":
                return {"Items": [{"Id": gid, "Name": name} for gid, name in state["groups"]]}
            if action == "CreateAssetGroup":
                group_id = f"group-{len(state['groups']) + 1}"
                state["groups"].append((group_id, body["Name"]))
                return {"Id": group_id}
            if action == "CreateAsset":
                return {"Id": f"asset-{state['calls'].count('CreateAsset')}"}
            if action == "GetAsset":
                return {"Id": body["Id"], "Status": "Active", "GroupId": "group-1", "AssetType": "Image"}
        raise AssertionError(action)


def asset_state():
    return {"lock": threading.Lock(), "calls": [], "groups": []}


@requires_comfyui
class ConcurrentAssetNodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = asset_state()
        uploads = self.uploads = []

        async def fake_upload(_cls, _image):
            uploads.append("image")
            await asyncio.sleep(0.05)
            return "https://storage.example/portrait.png"

        patches = [
            mock.patch.object(nodes_assets, "AssetLibrary", lambda _client: SlowAssetLibrary(self.state)),
            mock.patch.object(nodes_assets, "upload_image_to_comfy_storage", fake_upload),
            mock.patch.object(nodes_assets, "ASSET_POLL_SECONDS", 0),
            mock.patch.dict(nodes_assets.ASSET_UPLOAD_CACHE, clear=True),
            mock.patch.object(
                nodes_assets.BytePlusCreateImageAsset, "hidden", SimpleNamespace(unique_id="1", prompt={}), create=True
            ),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def run_node(self, **inputs):
        import torch

        node = nodes_assets.BytePlusCreateImageAsset
        result = await node.execute(ASSET_CLIENT, image=torch.ones((1, 320, 320, 3)), **inputs)
        return result.args

    async def test_concurrent_nodes_with_one_group_name_create_one_group(self):
        first, second = await asyncio.gather(
            self.run_node(group_name="Neon", asset_name="a"), self.run_node(group_name="Neon", asset_name="b")
        )
        self.assertEqual(self.state["calls"].count("CreateAssetGroup"), 1)
        self.assertEqual(first[1], second[1])
        self.assertEqual(nodes_assets.GROUP_LOCKS, {})

    async def test_concurrent_nodes_with_the_same_media_create_one_asset(self):
        first, second = await asyncio.gather(
            self.run_node(group_id="group-7", asset_name="same"),
            self.run_node(group_id="group-7", asset_name="same"),
        )
        self.assertEqual(self.state["calls"].count("CreateAsset"), 1)
        self.assertEqual(self.uploads, ["image"])
        self.assertEqual(first[0], second[0])
        self.assertEqual(nodes_assets.ASSET_UPLOAD_LOCKS, {})

    async def test_different_group_names_do_not_wait_for_each_other(self):
        await asyncio.gather(self.run_node(group_name="One"), self.run_node(group_name="Two"))
        self.assertEqual(self.state["calls"].count("CreateAssetGroup"), 2)


@requires_comfyui
class KeyedLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_entry_is_dropped_after_an_error(self):
        locks = {}
        with self.assertRaises(ValueError):
            async with nodes_shared.keyed_lock(locks, "k"):
                raise ValueError("boom")
        self.assertEqual(locks, {})

    async def test_waiting_for_the_lock_is_interruptible(self):
        locks = {}
        holder_entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with nodes_shared.keyed_lock(locks, "k"):
                holder_entered.set()
                await release.wait()

        task = asyncio.ensure_future(holder())
        await holder_entered.wait()
        with mock.patch.object(
            comfy.model_management, "throw_exception_if_processing_interrupted", side_effect=Interrupt()
        ):
            with self.assertRaises(Interrupt):
                async with nodes_shared.keyed_lock(locks, "k"):
                    self.fail("must not enter while the lock is held")
        release.set()
        await task
        self.assertEqual(locks, {})


class ScriptedLibrary:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0
        self.region = "ap-southeast-1"
        self.account_fingerprint = "acct-1"

    def call(self, action, body):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return {"Id": body["Id"], "Status": "Active"}


@requires_comfyui
class AssetPollingErrorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.object(nodes_assets, "ASSET_POLL_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_access_denied_stops_polling_at_once(self):
        library = ScriptedLibrary([nodes_assets.AssetApiError("[BytePlus] denied", retryable=False)] * 3)
        with self.assertRaisesRegex(nodes_assets.AssetApiError, "denied"):
            await nodes_assets.wait_for_asset(library, "asset-1", "default")
        self.assertEqual(library.calls, 1)

    async def test_retryable_errors_keep_waiting(self):
        library = ScriptedLibrary([nodes_assets.AssetApiError("[BytePlus] throttled", retryable=True)] * 2)
        asset = await nodes_assets.wait_for_asset(library, "asset-1", "default")
        self.assertEqual(asset["Status"], "Active")
        self.assertEqual(library.calls, 3)

    def test_error_classification(self):
        from byteplussdkcore.rest import ApiException

        def http_error(status, code):
            error = ApiException(status=status, reason="x")
            error.body = json.dumps({"ResponseMetadata": {"Error": {"Code": code, "Message": "m"}}})
            return error

        retryable = nodes_assets._retryable_asset_error
        self.assertFalse(retryable(http_error(403, "AccessDenied")))
        self.assertFalse(retryable(http_error(401, "InvalidAccessKey")))
        self.assertFalse(retryable(http_error(400, "InvalidParameter")))
        self.assertFalse(retryable(http_error(404, "NotFound")))
        self.assertTrue(retryable(http_error(429, "TooManyRequests")))
        self.assertTrue(retryable(http_error(400, "RequestLimitExceeded")))
        self.assertTrue(retryable(http_error(503, "ServiceUnavailable")))
        self.assertTrue(retryable(ApiException(status=0, reason="SSLError")))
        self.assertTrue(retryable(ApiException(status=200, reason={"Code": "Throttling", "Message": "slow"})))
        self.assertFalse(retryable(ApiException(status=200, reason={"Code": "AccessDenied", "Message": "no"})))

    def test_network_errors_from_the_sdk_are_retryable_asset_errors(self):
        import urllib3

        library = object.__new__(nodes_assets.AssetLibrary)
        failing = SimpleNamespace(do_call=mock.Mock(side_effect=urllib3.exceptions.MaxRetryError(None, "/", "down")))
        library._api = library._create_api = failing
        with self.assertRaises(nodes_assets.AssetApiError) as ctx:
            library.call("GetAsset", {"Id": "asset-1"})
        self.assertTrue(ctx.exception.retryable)
        self.assertTrue(str(ctx.exception).startswith("[BytePlus]"))



def two_colour_clip(width=320, height=240, frames=12):
    """An MP4 whose left half is red and right half blue."""
    import io

    import av
    import numpy

    buffer = io.BytesIO()
    pixels = numpy.zeros((height, width, 3), dtype=numpy.uint8)
    pixels[:, : width // 2, 0] = 255
    pixels[:, width // 2 :, 2] = 255
    with av.open(buffer, mode="w", format="mp4") as container:
        stream = container.add_stream("h264", rate=24)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        for _ in range(frames):
            for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    buffer.seek(0)
    return buffer


@requires_comfyui
class ReferenceVideoCropTests(unittest.TestCase):
    def test_scaling_a_cropped_video_keeps_only_the_crop(self):
        from comfy_api.latest import InputImpl

        cropped = InputImpl.VideoFromFile(two_colour_clip()).as_cropped(0, 0, 160, 240)
        self.assertEqual(cropped.get_dimensions(), (160, 240))
        scaled = nodes_seedance2.scale_video(cropped, 80, 120)
        self.assertEqual(scaled.get_dimensions(), (80, 120))
        frame = scaled.get_components().images[0]
        red, blue = float(frame[..., 0].mean()), float(frame[..., 2].mean())
        self.assertGreater(red, 0.8)
        self.assertLess(blue, 0.2)

    def test_untrimmed_uncropped_video_is_read_from_its_own_source(self):
        from comfy_api.latest import InputImpl

        source = two_colour_clip()
        video = InputImpl.VideoFromFile(source)
        self.assertIs(nodes_seedance2._video_stream_source(video), source)
        self.assertEqual(nodes_video.BytePlusVideoBase._video_variant(video), "")
        self.assertIsNot(nodes_seedance2._video_stream_source(video.as_cropped(0, 0, 160, 240)), source)
        self.assertIsNot(nodes_seedance2._video_stream_source(video.as_trimmed(0.1, 0.2, False)), source)


class QuietServer:
    def send_progress_text(self, *args, **kwargs):
        return None

    def send_sync(self, *args, **kwargs):
        return None


class StaggeredVideoTasks:
    """task-1 succeeds only on its second poll; task-2 at once, so polling finds task-2 first."""

    def __init__(self):
        self.created = 0
        self.polls = {}

    def create(self, **kwargs):
        self.created += 1
        return SimpleNamespace(id=f"task-{self.created}")

    def get(self, task_id):
        self.polls[task_id] = self.polls.get(task_id, 0) + 1
        done = task_id != "task-1" or self.polls[task_id] > 1
        return SimpleNamespace(id=task_id, status="succeeded" if done else "running", error=None)

    def delete(self, task_id):
        return None


@requires_comfyui
class SubmissionOrderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.object(executor.PromptServer, "instance", QuietServer(), create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_succeeded_tasks_come_back_in_submission_order(self):
        ark = SimpleNamespace(content_generation=SimpleNamespace(tasks=StaggeredVideoTasks()))
        client = SimpleNamespace(ark=ark, billed_ark=ark, api_key="key", region="ap-southeast-1")
        tasks = await executor.BytePlusGenerationExecutor(client, "7").run_batch_tasks(
            model_name="dreamina-seedance-2-0-260128",
            content=[{"type": "text", "text": "a cat"}],
            estimation_duration=5,
            resolution="720p",
            generation_count=2,
            non_blocking=False,
            non_blocking_cache_dict={},
            poll_interval=0,
        )
        self.assertEqual([task.id for task in tasks], ["task-1", "task-2"])

    async def test_draft_to_final_results_are_not_reordered_by_seed(self):
        # Final videos made from one draft share its seed; random seeds have no order at all.
        tasks = [
            SimpleNamespace(
                id=tid,
                seed=seed,
                content=SimpleNamespace(video_url=f"https://example.invalid/{tid}.mp4"),
                model_dump=lambda tid=tid: {"id": tid},
            )
            for tid, seed in (("final-1", 900), ("final-2", 5), ("final-3", 40))
        ]

        async def fake_download(_session, url, *_args):
            return f"/tmp/{os.path.basename(url)}"

        import torch

        with mock.patch.object(nodes_video, "download_video_to_temp", fake_download), mock.patch.object(
            nodes_video, "extract_last_frame_tensor", lambda path: torch.zeros(1, 4, 4, 3)
        ), mock.patch.object(nodes_video, "VideoFromFile", lambda path: path):
            video, _, response = (
                await nodes_video.BytePlusVideoBase()._handle_batch_success_async(tasks, 3, None)
            ).args
        self.assertEqual(video, ["/tmp/final-1.mp4", "/tmp/final-2.mp4", "/tmp/final-3.mp4"])
        self.assertEqual([item["id"] for item in json.loads(response)], ["final-1", "final-2", "final-3"])


@requires_comfyui
class VideoQueryTasksBoundsTests(unittest.TestCase):
    def test_page_inputs_follow_the_list_api_range(self):
        inputs = {i.id: i for i in nodes_video.BytePlusVideoQueryTasks.define_schema().inputs}
        for name in ("page_num", "page_size"):
            self.assertEqual((inputs[name].min, inputs[name].max), (1, 500), name)



def sine_audio(seconds=1.0, sample_rate=24000, channels=1):
    import math

    import torch

    t = torch.arange(int(seconds * sample_rate)) / sample_rate
    return {"waveform": (0.5 * torch.sin(2 * math.pi * 440 * t)).repeat(channels, 1)[None], "sample_rate": sample_rate}


@requires_comfyui
class SpeechMediaWorkOffTheLoopTests(unittest.IsolatedAsyncioTestCase):
    """Encoding and decoding audio and images runs in a worker thread, never on ComfyUI's event loop."""

    HEAVY = ("audio_to_wav_bytes", "decode_audio_bytes", "pcm16_to_audio", "_image_to_jpeg_base64",
             "context_image_jpeg")

    def setUp(self):
        self.loop_thread = threading.get_ident()
        self.ran = []
        for name in self.HEAVY:
            real = getattr(nodes_speech, name)

            def recorded(*args, _name=name, _real=real, **kwargs):
                self.ran.append((_name, threading.get_ident() == self.loop_thread))
                return _real(*args, **kwargs)

            patcher = mock.patch.object(nodes_speech, name, recorded)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = speech_api.SeedSpeechClient("speech-key-1")

    def serve(self, *responses):
        responses = list(responses)

        async def send(method, url, headers, body, timeout):
            status, headers_out, payload = responses.pop(0)
            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload).encode()
            elif isinstance(payload, str):
                payload = payload.encode()
            return speech_api.SpeechResponse(status, headers_out, payload)

        patcher = mock.patch.object(speech_api, "_send", send)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def fake_upload(self, *args):
        return "https://storage.example/file"

    def assert_off_the_loop(self, *names):
        self.assertEqual(sorted({name for name, _ in self.ran}), sorted(names))
        self.assertEqual([name for name, on_loop in self.ran if on_loop], [])

    def wav_payload(self):
        import base64

        wav = audio_utils.audio_to_wav_bytes(sine_audio(0.2))
        return {"code": 0, "audio": base64.b64encode(wav).decode()}

    async def test_seed_audio_with_an_audio_reference(self):
        self.serve((200, {}, self.wav_payload()))
        await nodes_speech.BytePlusSeedAudio.execute(
            self.client, "@Audio1 says hi",
            {"reference_mode": "audio reference", "reference_audio_1": sine_audio(2.0, 48000)},
            model="seed-audio-1.0",
        )
        self.assert_off_the_loop("audio_to_wav_bytes", "decode_audio_bytes")

    async def test_seed_audio_with_an_image_reference(self):
        import torch

        self.serve((200, {}, self.wav_payload()))
        await nodes_speech.BytePlusSeedAudio.execute(
            self.client, "A calm voice",
            {"reference_mode": "image reference", "reference_image": torch.ones((1, 32, 32, 3))},
            model="seed-audio-1.0",
        )
        self.assert_off_the_loop("_image_to_jpeg_base64", "decode_audio_bytes")

    async def test_tts(self):
        import base64

        stream = "\n".join(json.dumps(item) for item in (
            {"code": 0, "message": "", "data": base64.b64encode(b"\x00\x00\x00\x40").decode()},
            {"code": 20000000, "message": "ok", "data": None},
        ))
        self.serve((200, {}, stream))
        await nodes_speech.BytePlusSeedTTS.execute(self.client, "seed-tts-2.0", "Hi", "en_female_stokie_uranus_bigtts")
        self.assert_off_the_loop("pcm16_to_audio")

    async def test_fast_asr_with_a_context_image(self):
        import torch

        result = {"result": {"text": "Hello.", "utterances": []}, "audio_info": {"duration": 1000}}
        self.serve((200, {"X-Api-Status-Code": "20000000"}, result))
        with mock.patch.object(nodes_speech, "upload_to_comfy_storage", self.fake_upload):
            await nodes_speech.BytePlusSeedASR.execute(
                self.client, "seed-asr-fast", audio=sine_audio(), context_image=torch.rand((1, 64, 64, 3))
            )
        self.assert_off_the_loop("audio_to_wav_bytes", "context_image_jpeg")

    async def test_standard_asr_with_connected_audio(self):
        result = {"result": {"text": "Hello.", "utterances": []}, "audio_info": {"duration": 1000}}
        self.serve(
            (200, {"X-Api-Status-Code": "20000000"}, {}),
            (200, {"X-Api-Status-Code": "20000000"}, result),
        )
        with mock.patch.object(nodes_speech, "upload_to_comfy_storage", self.fake_upload), \
                mock.patch.object(nodes_speech, "SPEECH_ASR_POLL_SECONDS", 0):
            await nodes_speech.BytePlusSeedASR.execute(self.client, "seed-asr-2.0", audio=sine_audio())
        self.assert_off_the_loop("audio_to_wav_bytes")

    async def test_voice_clone(self):
        self.serve(
            (200, {}, {"code": 0, "speaker_id": "S_abc123", "status": 2,
                       "speaker_status": [{"model_type": 5, "demo_audio": "https://cdn.example/demo.wav"}]}),
            (200, {}, audio_utils.audio_to_wav_bytes(sine_audio(0.2))),
        )
        await nodes_speech.BytePlusSeedVoiceClone.execute(self.client, "S_abc123", sine_audio(2.0, 48000))
        self.assert_off_the_loop("audio_to_wav_bytes", "decode_audio_bytes")



@requires_comfyui
class LinkProbeRebindingTests(unittest.IsolatedAsyncioTestCase):
    """The probe session resolves through PublicOnlyResolver, so the address checked is the one connected to."""

    async def asyncSetUp(self):
        from aiohttp import web

        self.requests = []

        async def handle(request):
            self.requests.append(request.path)
            return web.Response(content_type="image/png")

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        await self.runner.cleanup()

    def resolve_to(self, address):
        import socket

        async def getaddrinfo(host, port, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0))]

        return mock.patch.object(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)

    async def test_a_host_that_rebinds_to_loopback_is_never_contacted(self):
        import aiohttp

        url = f"http://rebind.example:{self.port}/x.png"
        with self.resolve_to("127.0.0.1"):
            async with core_style._public_probe_session(aiohttp.ClientTimeout(total=5)) as session:
                with self.assertRaises(aiohttp.ClientError):
                    async with session.head(url):
                        pass
            self.assertEqual(self.requests, [])
            # Control: an ordinary session would have reached the private server.
            async with aiohttp.ClientSession() as session:
                async with session.head(url) as response:
                    self.assertEqual(response.status, 200)
        self.assertEqual(self.requests, ["/x.png"])

    async def test_resolver_refuses_mixed_answers_and_accepts_public_ones(self):
        import socket

        resolver = core_style.PublicOnlyResolver()
        mixed = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                 (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]
        with mock.patch.object(asyncio.get_running_loop(), "getaddrinfo", mock.AsyncMock(return_value=mixed)):
            with self.assertRaises(OSError):
                await resolver.resolve("mixed.example", 443)
        with self.resolve_to("93.184.216.34"):
            infos = await resolver.resolve("example.com", 443)
        self.assertEqual([info["host"] for info in infos], ["93.184.216.34"])
        await resolver.close()

    async def test_probe_through_a_rebinding_host_returns_nothing(self):
        # The first lookup (_host_is_public) sees a public address; the connection's lookup gets loopback.
        with mock.patch.object(core_style, "_host_is_public", mock.AsyncMock(return_value=True)), \
                self.resolve_to("127.0.0.1"):
            self.assertIsNone(await core_style._probe_url_kind(f"https://rebind.example:{self.port}/x.png"))
            self.assertIsNone(await core_style.sniff_audio_format(f"https://rebind.example:{self.port}/a"))
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
