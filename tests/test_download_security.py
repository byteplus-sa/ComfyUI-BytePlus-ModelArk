import asyncio
import importlib
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
import unittest
from unittest import mock

import aiohttp


COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
requires_comfyui = unittest.skipUnless(COMFY_ROOT, "Set COMFYUI_ROOT to a ComfyUI checkout to run these tests.")
if COMFY_ROOT:
    sys.path.insert(0, COMFY_ROOT)
    import utils  # noqa: F401, E402
    import comfy.cli_args  # noqa: E402

    comfy.cli_args.args.cpu = True
    PACKAGE_NAME = "byteplus_plugin_test"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [str(Path(__file__).resolve().parents[1])]
        sys.modules[PACKAGE_NAME] = package
    downloads = importlib.import_module(f"{PACKAGE_NAME}.nodes.utils_download")
    http_requests = importlib.import_module(f"{PACKAGE_NAME}.nodes.http_requests")


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


class Response:
    def __init__(self, status=200, location=None, body=b"image"):
        self.status = status
        self.headers = {"Location": location} if location else {}
        self.body = body
        self.content = self
        self.chunks = [body, b""]

    async def read(self, size=None):
        return self.body if size is None else self.chunks.pop(0)

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                types.SimpleNamespace(real_url="https://cdn.example/x"), (), status=self.status
            )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


@requires_comfyui
class PublicDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_response_can_be_fetched_or_streamed(self):
        self.assertEqual(
            await downloads._fetch_data_from_url_async(Session([Response()]), "https://cdn.example/x"), b"image"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            self.assertTrue(
                await downloads._download_to_file_stream_async(
                    Session([Response()]), "https://cdn.example/x", str(path)
                )
            )
            self.assertEqual(path.read_bytes(), b"image")

    async def test_private_or_malformed_initial_url_never_reaches_network(self):
        urls = [
            "",
            "file:///etc/passwd",
            "ftp://example.com/a",
            "https://127.0.0.1/a",
            "http://169.254.169.254/latest/meta-data",
            "https://10.0.0.1/a",
            "https://[::1]/a",
            "https://[::ffff:127.0.0.1]/a",
            "https://localhost./a",
            "https://host.local/a",
            "https://user:password@example.com/a",
            "https://example.com:invalid/a",
            "https://example.com/\nprivate",
        ]
        for url in urls:
            with self.subTest(url=url):
                session = Session([Response()])
                with self.assertRaises(aiohttp.InvalidURL):
                    await downloads._fetch_data_from_url_async(session, url)
                self.assertEqual(session.calls, [])

    async def test_relative_public_redirect_is_checked_and_preserves_signed_query(self):
        session = Session([Response(302, "/final?signature=signed"), Response()])
        result = await downloads._fetch_data_from_url_async(session, "https://cdn.example/start")
        self.assertEqual(result, b"image")
        self.assertEqual(
            [call[0] for call in session.calls],
            ["https://cdn.example/start", "https://cdn.example/final?signature=signed"],
        )
        self.assertTrue(all(call[1]["allow_redirects"] is False for call in session.calls))

    async def test_redirect_to_private_url_is_blocked_before_second_request(self):
        session = Session([Response(302, "http://127.0.0.1/private"), Response()])
        with self.assertRaises(aiohttp.InvalidURL):
            await downloads._fetch_data_from_url_async(session, "https://cdn.example/start")
        self.assertEqual(len(session.calls), 1)

    async def test_redirect_loop_and_missing_location_fail_without_retry(self):
        for responses in ([Response(302, "/loop") for _ in range(6)], [Response(302)]):
            session = Session(responses)
            with self.assertRaises(aiohttp.InvalidURL):
                await downloads._fetch_data_from_url_async(session, "https://cdn.example/start")
            self.assertLessEqual(len(session.calls), 6)

    async def test_service_requests_refuse_private_urls_before_opening_session(self):
        with mock.patch.object(aiohttp, "ClientSession") as session:
            with self.assertRaises(aiohttp.InvalidURL):
                await http_requests.send_http_request("GET", "http://127.0.0.1/private", {}, None, 5)
            session.assert_not_called()

    async def test_service_redirect_is_returned_without_forwarding_credentials(self):
        session = mock.MagicMock()
        session.__aenter__.return_value = session
        session.request.return_value = Response(302, "https://other.example/private")
        headers = {"Authorization": "Bearer test-only"}
        with mock.patch.object(http_requests, "public_download_session", return_value=session):
            status, response_headers, _ = await http_requests.send_http_request(
                "POST", "https://api.example/task", headers, {"prompt": "test"}, 5
            )
        self.assertEqual(status, 302)
        self.assertEqual(response_headers["Location"], "https://other.example/private")
        session.request.assert_called_once_with(
            "POST", "https://api.example/task", headers=headers, json={"prompt": "test"}, allow_redirects=False
        )

    async def test_invalid_url_is_a_permanent_request_failure(self):
        async def send():
            raise aiohttp.InvalidURL("Blocked URL")

        with self.assertRaises(http_requests.RetryableRequestError) as raised:
            await http_requests.await_request(send(), "timeout", lambda error: "invalid")
        self.assertFalse(raised.exception.retryable)

    async def test_rebinding_hostname_never_reaches_local_server(self):
        from aiohttp import web

        requests = []

        async def handle(request):
            requests.append(request.path)
            return web.Response(body=b"private")

        app = web.Application()
        app.router.add_get("/private", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]

            async def resolve(host, requested_port, *args, **kwargs):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", requested_port))]

            with mock.patch.object(asyncio.get_running_loop(), "getaddrinfo", resolve):
                async with downloads.public_download_session() as session:
                    with self.assertRaises(aiohttp.ClientError):
                        await downloads._fetch_data_from_url_async(
                            session, f"http://rebind.example:{port}/private", retries=0
                        )
                self.assertEqual(requests, [])
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"http://rebind.example:{port}/private") as response:
                        self.assertEqual(await response.read(), b"private")
            self.assertEqual(requests, ["/private"])
        finally:
            await runner.cleanup()
