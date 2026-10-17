"""
Fixes from the 2026-10-17 code review: signed result links in download logs,
retries of permanent download errors, file extensions taken from the query
string, the Ark Files upload cache shared across accounts and regions,
Layer Separation files overwriting each other, check_api_key treating any
non-401 status as accepted, and leftover debug code.

  COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_hardening
"""
import asyncio
import contextlib
import importlib
import inspect
import io
import json
import os
import re
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

    comfy.cli_args.args.cpu = True

    PACKAGE_NAME = "byteplus_plugin_test"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [PLUGIN_ROOT]
        sys.modules[PACKAGE_NAME] = package

    nodes_shared = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_shared")
    nodes_video = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_video")
    seedream_utils = importlib.import_module(f"{PACKAGE_NAME}.nodes.seedream_utils")
    utils_download = importlib.import_module(f"{PACKAGE_NAME}.nodes.utils_download")


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


SIGNED_URL = "https://cdn.example.com/results/out.mp4?X-Signature=top-secret-token"


class FakeResponse:
    def __init__(self, url, status, body=b"payload"):
        self.url = url
        self.status = status
        self.chunks = [body, b""]
        self.content = SimpleNamespace(read=self._read_chunk)

    async def _read_chunk(self, _size):
        return self.chunks.pop(0)

    async def read(self):
        return self.chunks[0]

    def raise_for_status(self):
        if self.status >= 400:
            import aiohttp

            raise aiohttp.ClientResponseError(
                SimpleNamespace(real_url=self.url), (), status=self.status, message="error"
            )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Answers each GET with the next status in the list."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        return FakeResponse(url, self.statuses.pop(0))


@requires_comfyui
class DownloadLogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.object(utils_download, "DOWNLOAD_RETRY_DELAY_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "out.bin")

    def test_the_raw_aiohttp_error_contains_the_signed_link(self):
        import aiohttp

        error = aiohttp.ClientResponseError(SimpleNamespace(real_url=SIGNED_URL), (), status=503)
        self.assertIn("top-secret-token", str(error))

    async def test_stream_retry_log_hides_the_signed_query(self):
        session = FakeSession([503, 200])
        with self.assertLogs("BytePlus", level="INFO") as logs:
            self.assertTrue(await utils_download._download_to_file_stream_async(session, SIGNED_URL, self.path))
        output = "\n".join(logs.output)
        self.assertNotIn("top-secret-token", output)
        self.assertIn("HTTP 503", output)
        self.assertEqual(session.calls, 2)

    async def test_fetch_retry_log_hides_the_signed_query(self):
        session = FakeSession([500, 200])
        with self.assertLogs("BytePlus", level="INFO") as logs:
            data = await utils_download._fetch_data_from_url_async(session, SIGNED_URL)
        self.assertEqual(data, b"payload")
        self.assertNotIn("top-secret-token", "\n".join(logs.output))

    async def test_permanent_client_errors_are_not_retried(self):
        import aiohttp

        for status in (400, 401, 403, 404, 410):
            for download in (
                lambda s: utils_download._download_to_file_stream_async(s, SIGNED_URL, self.path),
                lambda s: utils_download._fetch_data_from_url_async(s, SIGNED_URL),
            ):
                with self.subTest(status=status):
                    session = FakeSession([status, 200])
                    with self.assertRaises(aiohttp.ClientResponseError):
                        await download(session)
                    self.assertEqual(session.calls, 1)

    async def test_an_invalid_link_is_not_retried(self):
        import aiohttp

        class InvalidLinkSession:
            calls = 0

            def get(self, url, timeout=None):
                self.calls += 1
                raise aiohttp.InvalidURL(url)

        session = InvalidLinkSession()
        with self.assertRaises(aiohttp.InvalidURL):
            await utils_download._fetch_data_from_url_async(session, "not a link")
        self.assertEqual(session.calls, 1)

    async def test_timeouts_and_rate_limits_are_retried(self):
        for status in (408, 429, 502):
            with self.subTest(status=status):
                session = FakeSession([status, 200])
                with self.assertLogs("BytePlus", level="INFO"):
                    self.assertTrue(
                        await utils_download._download_to_file_stream_async(session, SIGNED_URL, self.path)
                    )
                self.assertEqual(session.calls, 2)

    async def test_retries_stop_after_the_last_attempt(self):
        import aiohttp

        session = FakeSession([503] * 4)
        with self.assertLogs("BytePlus", level="INFO"):
            with self.assertRaises(aiohttp.ClientResponseError):
                await utils_download._download_to_file_stream_async(session, SIGNED_URL, self.path, retries=3)
        self.assertEqual(session.calls, 4)

    async def test_failure_logs_hide_the_signed_query(self):
        session = FakeSession([403] * 3)
        with self.assertLogs("BytePlus", level="INFO") as logs, \
                mock.patch.object(utils_download.folder_paths, "get_temp_directory", lambda: self.tmp.name):
            self.assertIsNone(await utils_download.download_url_to_image_tensor_async(session, SIGNED_URL))
            self.assertIsNone(await utils_download.download_url_to_rgba_tensor_async(session, SIGNED_URL))
            tensor, path = await utils_download.download_image_to_temp(session, SIGNED_URL, "img", 1, "")
        self.assertEqual((tensor, path), (None, None))
        output = "\n".join(logs.output)
        self.assertNotIn("top-secret-token", output)
        self.assertIn("https://cdn.example.com/results/out.mp4", output)
        self.assertIn("HTTP 403", output)


@requires_comfyui
class DownloadExtensionTests(unittest.IsolatedAsyncioTestCase):
    def test_extension_comes_from_the_link_path(self):
        cases = {
            "https://cdn.example.com/a/out.mp4?sig=a.b": "mp4",
            "https://cdn.example.com/a/out.WEBM": "WEBM",
            "https://cdn.example.com/a/noext?sig=a.b.c": "default",
            "https://cdn.example.com/a/noext": "default",
            "https://cdn.example.com": "default",
            "https://cdn.example.com/a/out.mp4%2F..%2Fx": "default",
            "https://cdn.example.com/a/out.": "default",
            "https://cdn.example.com/a/out.tar.gz#frag.x": "gz",
            "": "default",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(utils_download.url_file_extension(url, "default"), expected)

    async def test_downloads_are_saved_with_the_path_extension(self):
        async def fake_stream(session, url, path, timeout=None, retries=3):
            with open(path, "wb") as handle:
                handle.write(b"video")
            return True

        async def fake_fetch(session, url, timeout=None, retries=3):
            return b"not an image"

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(utils_download.folder_paths, "get_temp_directory", lambda: tmp), \
                mock.patch.object(utils_download, "_download_to_file_stream_async", fake_stream), \
                mock.patch.object(utils_download, "_fetch_data_from_url_async", fake_fetch):
            video = await utils_download.download_video_to_temp(
                None, "https://cdn.example.com/v/abc?X-Tos-Signature=a.b", "video", 7, ""
            )
            _tensor, image = await utils_download.download_image_to_temp(
                None, "https://cdn.example.com/i/frame.png?X-Tos-Signature=x.y", "frame", 7, ""
            )
        self.assertTrue(video.endswith(".mp4"))
        self.assertTrue(image.endswith(".png"))


@requires_comfyui
class LeftoverDebugCodeTests(unittest.TestCase):
    COMMENTED_DEBUG = re.compile(r"^\s*#\s*(print\(|log_msg\(|t\d\s*=\s*time\.time\(\))")

    def read(self, name):
        with open(os.path.join(PLUGIN_ROOT, "nodes", name), encoding="utf-8") as handle:
            return handle.read().splitlines()

    def test_no_commented_out_debug_code(self):
        for name in ("utils_download.py", "executor.py", "nodes_video.py"):
            for number, line in enumerate(self.read(name), start=1):
                with self.subTest(file=name, line=number):
                    self.assertIsNone(self.COMMENTED_DEBUG.match(line), line)

    def test_unknown_video_model_uses_the_message_table(self):
        with self.assertRaises(nodes_shared.BytePlusException) as ctx:
            nodes_video.resolve_model_id("no-such-model")
        self.assertEqual(
            str(ctx.exception), nodes_shared.get_text("err_model_not_supported", model="no-such-model")
        )

    def test_query_errors_are_logged_not_printed(self):
        def failing_list(**_kwargs):
            raise RuntimeError("listing failed")

        client = SimpleNamespace(ark=SimpleNamespace(content_generation=SimpleNamespace(
            tasks=SimpleNamespace(list=failing_list)
        )))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), self.assertLogs("BytePlus", level="INFO") as logs:
            asyncio.run(nodes_video.BytePlusVideoQueryTasks.execute(client, 1, 10, "all", "", "", "all", 0))
        self.assertEqual(stdout.getvalue(), "")
        self.assertTrue(any("listing failed" in line for line in logs.output))
        self.assertIn("query_tasks_partial_error", nodes_shared.MESSAGES)


class FakeFiles:
    """Ark Files API: create counts uploads; retrieve answers per file ID."""

    def __init__(self, retrieve_errors=None):
        self.created = []
        self.retrieve_errors = retrieve_errors or {}

    def create(self, **kwargs):
        file_id = f"file-{len(self.created) + 1}"
        self.created.append(file_id)
        return SimpleNamespace(id=file_id, status="active")

    def retrieve(self, file_id):
        import time

        error = self.retrieve_errors.get(file_id)
        if error is not None:
            raise error
        return SimpleNamespace(status="active", expire_at=int(time.time()) + 86400)

    def delete(self, file_id):
        pass


class StatusError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


@requires_comfyui
class UploadCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache_file = os.path.join(self.tmp.name, "files_upload_cache.json")
        store = nodes_shared.UploadCacheStore(self.cache_file)
        for patcher in (
            mock.patch.object(nodes_shared, "UPLOAD_CACHE_STORE", store),
            mock.patch.object(nodes_shared, "FILE_ACTIVE_POLL_SECONDS", 0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.image = os.path.join(self.tmp.name, "image.jpg")
        with open(self.image, "wb") as handle:
            handle.write(os.urandom(64))

    def client(self, files, api_key="key-a", region="ap-southeast-1"):
        return nodes_shared.BytePlusClients(SimpleNamespace(files=files), api_key=api_key, region=region)

    async def upload(self, client):
        with self.assertLogs("BytePlus", level="INFO"):
            return await nodes_shared.upload_file_to_ark(client, self.image)

    async def test_same_account_reuses_the_upload(self):
        files = FakeFiles()
        first = await self.upload(self.client(files))
        second = await self.upload(self.client(files))
        self.assertEqual((first, second), ("file-1", "file-1"))
        self.assertEqual(files.created, ["file-1"])

    async def test_another_key_uploads_again(self):
        files = FakeFiles()
        await self.upload(self.client(files, api_key="key-a"))
        other = await self.upload(self.client(files, api_key="key-b"))
        self.assertEqual(other, "file-2")
        self.assertEqual(files.created, ["file-1", "file-2"])

    async def test_another_region_uploads_again(self):
        files = FakeFiles()
        await self.upload(self.client(files, region="ap-southeast-1"))
        other = await self.upload(self.client(files, region="eu-west-1"))
        self.assertEqual(other, "file-2")

    async def test_the_cache_file_never_holds_the_key(self):
        await self.upload(self.client(FakeFiles(), api_key="very-secret-api-key"))
        with open(self.cache_file, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("very-secret-api-key", text)
        self.assertIn(nodes_shared.api_key_fingerprint("very-secret-api-key"), text)

    async def test_a_missing_cached_file_is_uploaded_again(self):
        files = FakeFiles()
        await self.upload(self.client(files))
        for error in (StatusError(404), StatusError(403), StatusError(401)):
            with self.subTest(status=error.status_code):
                files.retrieve_errors = {files.created[-1]: error}
                fresh = await self.upload(self.client(files))
                self.assertEqual(fresh, files.created[-1])
                self.assertNotIn(fresh, files.retrieve_errors)

    async def test_a_transient_retrieve_error_keeps_the_cached_file(self):
        import httpx
        from byteplussdkarkruntime._exceptions import ArkAPIConnectionError, ArkAPITimeoutError

        request = httpx.Request("GET", "https://ark.example/api/v3/files/file-1")
        files = FakeFiles()
        await self.upload(self.client(files))
        for error in (
            StatusError(500), StatusError(503),
            ArkAPIConnectionError(request=request, request_id="r"),
            ArkAPITimeoutError(request=request, request_id="r"),
        ):
            with self.subTest(error=type(error).__name__):
                files.retrieve_errors = {"file-1": error}
                self.assertEqual(await self.upload(self.client(files)), "file-1")
        self.assertEqual(files.created, ["file-1"])

    def test_cache_entries_without_an_account_are_dropped(self):
        import time

        expire_at = int(time.time()) + 86400
        old_entries = {
            json.dumps([self.image, None]): {"file_id": "old-1", "expire_at": expire_at},
            json.dumps([self.image, None, 604800]): {"file_id": "old-2", "expire_at": expire_at},
        }
        with open(self.cache_file, "w", encoding="utf-8") as handle:
            json.dump(old_entries, handle)
        store = nodes_shared.UploadCacheStore(self.cache_file)
        store.load()
        self.assertEqual(store.keys(), [])

    def test_upload_takes_no_return_meta(self):
        self.assertNotIn("return_meta", inspect.signature(nodes_shared.upload_file_to_ark).parameters)


@requires_comfyui
class LayerFileTests(unittest.TestCase):
    def save(self, count):
        import PIL.Image

        base = PIL.Image.new("RGBA", (8, 8), (255, 0, 0, 255))
        layers = [PIL.Image.new("RGBA", (4, 4), (0, 0, 255 - i, 255)) for i in range(count)]
        with mock.patch.object(seedream_utils.folder_paths, "get_output_directory", lambda: self.tmp.name):
            return seedream_utils._save_layer_pngs("BytePlus/Test/Layers", base, layers, "png")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_every_layer_keeps_its_own_file_and_the_base_is_not_overwritten(self):
        import PIL.Image

        saved = self.save(4)
        self.assertEqual(len(saved), 5)
        self.assertEqual(len(set(saved)), 5)
        self.assertTrue(saved[0].endswith("_base.png"))
        for position, relative in enumerate(saved[1:], start=1):
            self.assertTrue(relative.endswith(f"_layer{position:02d}.png"), relative)
        for relative in saved:
            self.assertTrue(os.path.exists(os.path.join(self.tmp.name, relative)))
        with PIL.Image.open(os.path.join(self.tmp.name, saved[0])) as base:
            self.assertEqual(base.size, (8, 8))
            self.assertEqual(base.getpixel((0, 0)), (255, 0, 0, 255))
        with PIL.Image.open(os.path.join(self.tmp.name, saved[2])) as second:
            self.assertEqual(second.getpixel((0, 0)), (0, 0, 254, 255))


@requires_comfyui
class CheckApiKeyTests(unittest.TestCase):
    def check(self, status=None, error=None):
        def fake_get(*_args, **_kwargs):
            if error is not None:
                raise error
            return SimpleNamespace(status_code=status)

        with mock.patch.object(nodes_shared.requests, "get", fake_get), \
                self.assertLogs("BytePlus", level="INFO") if error is not None else contextlib.nullcontext():
            return nodes_shared.check_api_key("key", "https://ark.example/api/v3")

    def test_any_answer_that_does_not_refuse_the_key_is_accepted(self):
        for status in (200, 204, 301, 404):
            with self.subTest(status=status):
                self.assertIs(self.check(status), True)

    def test_unauthorized_and_forbidden_are_rejected(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.assertIs(self.check(status), False)

    def test_timeouts_rate_limits_and_server_errors_leave_the_key_unchecked(self):
        for status in (408, 429, 500, 502, 503):
            with self.subTest(status=status):
                self.assertIsNone(self.check(status))

    def test_an_unreachable_endpoint_leaves_the_key_unchecked(self):
        self.assertIsNone(self.check(error=OSError("network down")))


if __name__ == "__main__":
    unittest.main()
