"""
Stop and partial failures around paid requests: parallel Seedream requests, Seedance
task creation, 3D task creation, the non_blocking task cache, the generation_count cap
and Image Enhance batches. Fake clients only; nothing calls the API.

  COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_interrupt_fixes
"""

import asyncio
import importlib
import os
import shutil
import sys
import tempfile
import threading
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
    constants = importlib.import_module(f"{PACKAGE_NAME}.nodes.constants")
    core_style = importlib.import_module(f"{PACKAGE_NAME}.nodes.core_style")
    executor = importlib.import_module(f"{PACKAGE_NAME}.nodes.executor")
    nodes_3d = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_3d")
    nodes_mediakit = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_mediakit")
    nodes_shared = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_shared")
    nodes_seedream = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_seedream")
    nodes_speech = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_speech")
    import comfy.model_management
    from comfy_execution.graph_utils import ExecutionBlocker

    Interrupt = comfy.model_management.InterruptProcessingException


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


class QuietServer:
    def send_progress_text(self, *args, **kwargs):
        return None

    def send_sync(self, *args, **kwargs):
        return None


class FakeVideoTasks:
    """Seedance tasks: create returns task-N (or raises a scripted error), get answers a status."""

    def __init__(self, create_errors=None, status="running"):
        self.create_errors = dict(create_errors or {})
        self.status = status
        self.created = []
        self.deleted = []
        self.lock = threading.Lock()

    def create(self, **kwargs):
        with self.lock:
            number = len(self.created) + 1
            self.created.append(kwargs)
        error = self.create_errors.get(number)
        if error is not None:
            raise error
        return SimpleNamespace(id=f"task-{number}")

    def get(self, task_id):
        return SimpleNamespace(id=task_id, status=self.status, error=None)

    def delete(self, task_id):
        self.deleted.append(task_id)

    def list(self, **kwargs):
        return SimpleNamespace(items=[])


def video_client(tasks, api_key="key-one", region="ap-southeast-1"):
    ark = SimpleNamespace(content_generation=SimpleNamespace(tasks=tasks))
    return SimpleNamespace(ark=ark, billed_ark=ark, api_key=api_key, region=region)


def solid(width, height, channel):
    import torch

    image = torch.zeros(1, height, width, 3)
    image[..., channel] = 1.0
    return image


@requires_comfyui
class ExecutorTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.object(executor.PromptServer, "instance", QuietServer(), create=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class ParallelImageRequestTests(ExecutorTestCase):
    async def test_interrupt_cancels_the_other_requests_and_is_reraised(self):
        cancelled = []

        async def request(index, session):
            if index == 0:
                await asyncio.sleep(0.05)
                raise Interrupt()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append(index)
                raise

        runner = executor.BytePlusGenerationExecutor(video_client(FakeVideoTasks()), "7")
        with self.assertRaises(Interrupt):
            await asyncio.wait_for(runner.run_parallel_requests(3, request), 5)
        self.assertEqual(sorted(cancelled), [1, 2])

    async def test_a_failed_request_keeps_the_others(self):
        async def request(index, session):
            if index == 1:
                raise nodes_seedream.BytePlusException("[BytePlus] refused")
            return f"image-{index}", {"batch_index": index}

        runner = executor.BytePlusGenerationExecutor(video_client(FakeVideoTasks()), "7")
        tensors, metadata = await runner.run_parallel_requests(3, request)
        self.assertEqual(tensors, ["image-0", "image-2"])
        self.assertEqual([m["batch_index"] for m in metadata], [0, 2])


def run_videos(client, cache, non_blocking=True, generation_count=1, text="a cat", node_id="7", seed=None):
    return executor.BytePlusGenerationExecutor(client, node_id).run_batch_tasks(
        model_name="dreamina-seedance-2-0-260128",
        content=[{"type": "text", "text": text}],
        estimation_duration=5,
        resolution="720p",
        generation_count=generation_count,
        non_blocking=non_blocking,
        non_blocking_cache_dict=cache,
        poll_interval=0,
        extra_api_params={"seed": seed} if seed is not None else None,
    )


class VideoCreationInterruptTests(ExecutorTestCase):
    async def test_interrupt_during_creation_cancels_the_created_tasks(self):
        tasks = FakeVideoTasks(create_errors={2: Interrupt()})
        with self.assertRaises(Interrupt):
            await run_videos(video_client(tasks), {}, non_blocking=False, generation_count=3)
        self.assertEqual(len(tasks.created), 3)
        self.assertEqual(sorted(tasks.deleted), ["task-1", "task-3"])

    async def test_interrupt_on_a_single_task_creation_is_reraised(self):
        tasks = FakeVideoTasks(create_errors={1: Interrupt()})
        cache = {}
        with self.assertRaises(Interrupt):
            await run_videos(video_client(tasks), cache)
        self.assertEqual(tasks.deleted, [])
        self.assertEqual(cache, {})


def plain(text):
    return text.removeprefix("[BytePlus] ")


class PollTextTests(unittest.TestCase):
    def test_single_running_task_line(self):
        text = plain(executor._poll_console_text(1, ["t1"], 0, 1, 1, 0, 12, 60))
        self.assertEqual(text, "Task t1: Running... 12s / 60s elapsed")

    def test_single_queued_task_line_shows_its_status(self):
        text = plain(executor._poll_console_text(1, ["t1"], 0, 1, 0, 1, 5, None, "queued"))
        self.assertEqual(text, "Task t1: Queued and waiting for resources... (Status: queued)")

    def test_batch_line_while_running_uses_the_estimate(self):
        text = plain(executor._poll_console_text(3, ["a", "b"], 1, 3, 2, 0, 30, 90))
        self.assertEqual(text, "Batch Progress: 1/3 done. 2 pending... (Elapsed 30s / 90s) [Run: 2, Queue: 0]")

    def test_batch_line_while_queued_estimates_ten_more_seconds(self):
        text = plain(executor._poll_console_text(3, ["a", "b", "c"], 0, 3, 0, 3, 30, None, "queued"))
        self.assertEqual(text, "Batch Progress: 0/3 done. 3 pending... (Elapsed 30s / 40s) [Run: 0, Queue: 3]")

    def test_node_status_text_adds_time_left_and_the_long_wait_hint_only_when_due(self):
        short = executor._poll_node_status_text(1, 0, 0, 1, 30, 20)
        self.assertIn("1 running, 0 queued, 0/1 done · waiting 30s", short)
        self.assertIn("about 20s left", short)
        self.assertNotIn("Waiting for", short)
        long = executor._poll_node_status_text(0, 2, 0, 2, executor.LONG_WAIT_HINT_SECONDS, 0)
        self.assertIn("0 running, 2 queued, 0/2 done · waiting 1200s", long)
        self.assertNotIn("left", long)
        self.assertIn("Waiting for 20 min already", long)


class NonBlockingCacheTests(ExecutorTestCase):
    async def test_same_request_collects_the_pending_task(self):
        tasks = FakeVideoTasks()
        cache = {}
        first = await run_videos(video_client(tasks), cache)
        second = await run_videos(video_client(tasks), cache)
        self.assertEqual(first["task_ids"], ["task-1"])
        self.assertEqual(second["task_ids"], ["task-1"])
        self.assertEqual(len(tasks.created), 1)

    async def test_collecting_a_failed_task_reports_the_failure_and_forgets_it(self):
        tasks = FakeVideoTasks()
        cache = {}
        await run_videos(video_client(tasks), cache)
        tasks.status = "failed"
        with self.assertRaisesRegex(nodes_seedream.BytePlusException, "task-1"):
            await run_videos(video_client(tasks), cache)
        self.assertEqual(cache, {})

    async def test_changed_prompt_submits_a_new_task(self):
        tasks = FakeVideoTasks()
        cache = {}
        await run_videos(video_client(tasks), cache, text="a cat")
        second = await run_videos(video_client(tasks), cache, text="a dog")
        self.assertEqual(len(tasks.created), 2)
        self.assertEqual(tasks.created[1]["content"][0]["text"], "a dog")
        self.assertEqual(second["task_ids"], ["task-2"])

    async def test_changed_generation_count_submits_new_tasks(self):
        tasks = FakeVideoTasks()
        cache = {}
        await run_videos(video_client(tasks), cache)
        second = await run_videos(video_client(tasks), cache, generation_count=2)
        # Both tasks are created in parallel threads, so the fake may number them in either order.
        self.assertEqual(sorted(second["task_ids"]), ["task-2", "task-3"])

    async def test_another_key_or_region_submits_a_new_task(self):
        tasks = FakeVideoTasks()
        cache = {}
        await run_videos(video_client(tasks), cache)
        await run_videos(video_client(tasks, api_key="key-two"), cache)
        await run_videos(video_client(tasks, api_key="key-two", region="eu-west-1"), cache)
        self.assertEqual(len(tasks.created), 3)

    async def test_the_cache_never_holds_the_key(self):
        cache = {}
        await run_videos(video_client(FakeVideoTasks(), api_key="secret-key-value"), cache)
        self.assertNotIn("secret-key-value", repr(cache))

    async def test_a_new_seed_alone_still_collects_the_pending_task(self):
        tasks = FakeVideoTasks()
        cache = {}
        await run_videos(video_client(tasks), cache, seed=111)
        second = await run_videos(video_client(tasks), cache, seed=222)
        self.assertEqual(len(tasks.created), 1)
        self.assertEqual(second["task_ids"], ["task-1"])

    async def test_a_dropped_pending_entry_names_its_tasks(self):
        tasks = FakeVideoTasks()
        cache = {}
        await run_videos(video_client(tasks), cache, text="a cat")
        with mock.patch.object(executor, "log_msg") as log:
            await run_videos(video_client(tasks), cache, text="a dog")
        dropped = [c for c in log.call_args_list if c.args and c.args[0] == "non_blocking_dropped"]
        self.assertEqual(len(dropped), 1)
        self.assertIn("task-1", dropped[0].kwargs["ids"])

    async def test_a_blocking_run_drops_the_pending_entry(self):
        tasks = FakeVideoTasks(status="succeeded")
        cache = {"7": {"task_ids": ["old-task"], "request": "anything"}}
        await run_videos(video_client(tasks), cache, non_blocking=False)
        self.assertNotIn("7", cache)
        self.assertEqual(len(tasks.created), 1)


@requires_comfyui
class ThreeDSubmitInterruptTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupt_while_creating_cancels_the_task_once_created(self):
        release = threading.Event()
        created, deleted = [], []

        def create(**kwargs):
            release.wait(5)
            created.append(kwargs)
            return SimpleNamespace(id="task-3d")

        def delete(task_id):
            deleted.append(task_id)

        tasks = SimpleNamespace(create=create, delete=delete)
        ark = SimpleNamespace(content_generation=SimpleNamespace(tasks=tasks))
        client = SimpleNamespace(ark=ark, billed_ark=ark)

        def interrupt_once():
            release.set()
            raise Interrupt()

        with (
            mock.patch.object(
                nodes_3d.comfy.model_management,
                "throw_exception_if_processing_interrupted",
                side_effect=interrupt_once,
            ),
            self.assertRaises(Interrupt),
        ):
            await nodes_3d.submit_task(client, {"model": "hyper3d-gen2-260112", "content": [], "seed": 1})
        self.assertEqual(len(created), 1)
        self.assertEqual(deleted, ["task-3d"])

    async def test_interrupt_raised_by_the_creation_itself_cancels_nothing(self):
        deleted = []

        def create(**kwargs):
            raise Interrupt()

        tasks = SimpleNamespace(create=create, delete=deleted.append)
        ark = SimpleNamespace(content_generation=SimpleNamespace(tasks=tasks))
        with self.assertRaises(Interrupt):
            await nodes_3d.submit_task(SimpleNamespace(ark=ark, billed_ark=ark), {"model": "m", "content": []})
        self.assertEqual(deleted, [])


@requires_comfyui
class GenerationCountCapTests(unittest.TestCase):
    def test_every_generation_count_input_is_capped(self):
        self.assertEqual(constants.MAX_GENERATION_COUNT, 16)
        for seeded in (True, False):
            with self.subTest(seeded=seeded):
                self.assertEqual(core_style.generation_count_input(seeded).max, constants.MAX_GENERATION_COUNT)

    def test_seed_audio_uses_the_same_cap(self):
        schema = nodes_speech.BytePlusSeedAudio.define_schema()
        count = next(i for i in schema.inputs if i.id == "generation_count")
        self.assertEqual(count.max, constants.MAX_GENERATION_COUNT)


@requires_comfyui
class ImageEnhanceFixtures(unittest.IsolatedAsyncioTestCase):
    """Fake MediaKit client, downloads and uploads for the Image Enhance tests (no tests of its own)."""

    RESULT = {
        "image_url": "https://cdn.example/out.png?auth_key=x",
        "image_format": "png",
        "image_width": 80,
        "image_height": 60,
    }

    def setUp(self):
        from PIL import Image

        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.enhanced = os.path.join(self.tmp, "enhanced.png")
        Image.new("RGB", (80, 60), (0, 0, 255)).save(self.enhanced)
        self.source_fails = False
        self.downloads = 0
        test = self

        async def fake_download(url, prefix, ext="mp4"):
            test.downloads += 1
            if prefix == "image_source" and test.source_fails:
                raise nodes_mediakit.BytePlusException("[BytePlus] source link expired")
            copy = os.path.join(test.tmp, f"download_{test.downloads}.{ext}")
            shutil.copy(test.enhanced, copy)
            return copy

        async def fake_upload(cls, data, filename, mime_type, wait_label=None):
            return f"https://storage.example/{filename}"

        for patcher in (
            mock.patch.object(nodes_mediakit, "_download", fake_download),
            mock.patch("comfy_api_nodes.util.upload_file_to_comfyapi", fake_upload, create=True),
            mock.patch.dict(nodes_shared.COMFY_UPLOAD_CACHE, clear=True),
            mock.patch.object(
                nodes_mediakit.BytePlusImageEnhance, "hidden", SimpleNamespace(unique_id="9", prompt={}), create=True
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = nodes_mediakit.MediaKitClient("test-key")


@requires_comfyui
class ImageEnhancePartialFailureTests(ImageEnhanceFixtures):
    def send_failing(self, failing_calls):
        calls = []

        async def send(client, method, path, body=None, timeout_seconds=None):
            calls.append(body)
            if len(calls) in failing_calls:
                return 400, {"success": False, "error": {"code": "BadImage", "message": "nope"}}
            return 200, {"success": True, "task_id": f"amk-{len(calls)}", "request_id": "r", "result": self.RESULT}

        return send

    async def run_node(self, send, **kwargs):
        args = dict(tool_version={"tool_version": "standard"}, output_size={"output_size": "multiple", "multiple": 2.0})
        args.update(kwargs)
        with (
            mock.patch.object(nodes_mediakit, "IMAGE_MAX_PARALLEL", 1),
            mock.patch.object(nodes_mediakit, "_send", send),
        ):
            return await nodes_mediakit.BytePlusImageEnhance.execute(self.client, **args)

    async def test_a_failed_image_keeps_the_other_paid_results(self):
        import json
        import torch

        batch = torch.cat([solid(40, 30, 0), solid(40, 30, 1), solid(40, 30, 2)])
        enhanced, original, response = (await self.run_node(self.send_failing({2}), image=batch)).args
        self.assertEqual(tuple(enhanced.shape), (2, 60, 80, 3))
        self.assertEqual(tuple(original.shape), (2, 60, 80, 3))
        self.assertGreater(float(original[0, 30, 40, 0]), 0.7)  # first input: red
        self.assertGreater(float(original[1, 30, 40, 2]), 0.7)  # third input: blue
        self.assertEqual([r["task_id"] for r in json.loads(response)], ["amk-1", "amk-3"])

    async def test_every_image_failing_raises_the_first_error(self):
        import torch

        batch = torch.cat([solid(40, 30, 0), solid(40, 30, 1)])
        with self.assertRaisesRegex(Exception, "BadImage"):
            await self.run_node(self.send_failing({1, 2}), image=batch)

    async def test_source_download_failure_still_returns_the_enhanced_image(self):
        self.source_fails = True
        enhanced, original, _ = (
            await self.run_node(self.send_failing(set()), image_url="https://cdn.example/in.jpg")
        ).args
        self.assertEqual(tuple(enhanced.shape), (1, 60, 80, 3))
        self.assertIsInstance(original, ExecutionBlocker)


@requires_comfyui
class ImageEnhanceRateLimitTests(ImageEnhanceFixtures):
    """MediaKit answers parallel sync calls with 429 RequestLimitExceeded (checked live 2026-10-18)."""

    def setUp(self):
        super().setUp()

        async def no_wait(seconds):
            return None

        patcher = mock.patch.object(nodes_mediakit, "sleep_interruptible", no_wait)
        patcher.start()
        self.addCleanup(patcher.stop)

    def send_scripted(self, statuses):
        calls = []
        active = {"now": 0, "max": 0}

        async def send(client, method, path, body=None, timeout_seconds=None):
            calls.append(body)
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            try:
                await asyncio.sleep(0.01)
                status = statuses[len(calls) - 1] if len(calls) <= len(statuses) else 200
                if status == 429:
                    error = {"code": "RequestLimitExceeded", "type": "TooManyRequests", "message": "rate limit"}
                    return 429, {"success": False, "error": error}
                if status >= 400:
                    return status, {"success": False, "error": {"code": "InternalError", "message": "boom"}}
                return 200, {"success": True, "task_id": f"amk-{len(calls)}", "request_id": "r", "result": self.RESULT}
            finally:
                active["now"] -= 1

        return send, calls, active

    async def run_node(self, send, **kwargs):
        args = dict(tool_version={"tool_version": "standard"}, output_size={"output_size": "multiple", "multiple": 2.0})
        args.update(kwargs)
        with mock.patch.object(nodes_mediakit, "_send", send):
            return await nodes_mediakit.BytePlusImageEnhance.execute(self.client, **args)

    async def test_rate_limited_calls_are_retried_until_accepted(self):
        send, calls, _ = self.send_scripted([429, 429, 200])
        enhanced, _, _ = (await self.run_node(send, image_url="https://cdn.example/in.jpg")).args
        self.assertEqual(tuple(enhanced.shape), (1, 60, 80, 3))
        self.assertEqual(len(calls), 3)

    async def test_a_batch_sends_one_enhance_call_at_a_time(self):
        import torch

        send, calls, active = self.send_scripted([])
        batch = torch.cat([solid(40, 30, 0), solid(40, 30, 1), solid(40, 30, 2)])
        enhanced, _, _ = (await self.run_node(send, image=batch)).args
        self.assertEqual(tuple(enhanced.shape), (3, 60, 80, 3))
        self.assertEqual(active["max"], 1)

    async def test_rate_limit_retries_give_up_after_the_cap(self):
        send, calls, _ = self.send_scripted([429] * 50)
        with self.assertRaisesRegex(Exception, "RequestLimitExceeded"):
            await self.run_node(send, image_url="https://cdn.example/in.jpg")
        self.assertEqual(len(calls), nodes_mediakit.IMAGE_RATE_LIMIT_RETRIES + 1)

    async def test_a_server_error_is_not_retried_because_the_image_may_be_billed(self):
        send, calls, _ = self.send_scripted([503, 200])
        with self.assertRaisesRegex(Exception, "InternalError"):
            await self.run_node(send, image_url="https://cdn.example/in.jpg")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
