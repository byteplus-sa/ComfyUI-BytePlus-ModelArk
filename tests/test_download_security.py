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
    nodes_shared = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_shared")
    core_style = importlib.import_module(f"{PACKAGE_NAME}.nodes.core_style")
    constants = importlib.import_module(f"{PACKAGE_NAME}.nodes.constants")
    las_api = importlib.import_module(f"{PACKAGE_NAME}.nodes.las_api")


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
            "https://example.com/\x00private",
            "https://example.com/\x7fprivate",
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
        url = constants.MEDIAKIT_REGION_BASE_URLS["ap-southeast-1"] + "/task"
        with mock.patch.object(http_requests, "public_download_session", return_value=session):
            status, response_headers, _ = await http_requests.send_http_request(
                "POST", url, headers, {"prompt": "test"}, 5
            )
        self.assertEqual(status, 302)
        self.assertEqual(response_headers["Location"], "https://other.example/private")
        session.request.assert_called_once_with(
            "POST", url, headers=headers, json={"prompt": "test"}, allow_redirects=False
        )

    async def test_las_submit_and_poll_use_fixed_origin_without_following_redirects(self):
        client = las_api.LASClient("test-only")
        for path in ("/api/v1/submit", "/api/v1/poll"):
            for status in (200, 302):
                with self.subTest(path=path, status=status):
                    session = mock.MagicMock()
                    session.__aenter__.return_value = session
                    session.request.return_value = Response(
                        status, "https://other.example/private", body=b'{"task_id":"test-task"}'
                    )
                    with mock.patch.object(http_requests, "public_download_session", return_value=session):
                        result = await las_api._send(client, "POST", path, {"test": True})
                    self.assertEqual(result, (status, {"task_id": "test-task"}))
                    session.request.assert_called_once_with(
                        "POST",
                        constants.LAS_REGION_BASE_URLS["ap-southeast-1"] + path,
                        headers={"Authorization": "Bearer test-only", "Content-Type": "application/json"},
                        json={"test": True},
                        allow_redirects=False,
                    )

    async def test_api_credentials_are_never_sent_outside_their_provider(self):
        cases = (
            ("https://evil.example/task", {"Authorization": "Bearer test-only"}),
            ("http://ark.ap-southeast.bytepluses.com/api/v3", {"Authorization": "Bearer test-only"}),
            ("https://ark.ap-southeast.bytepluses.com:8443/api/v3", {"Authorization": "Bearer test-only"}),
            ("https://ark.ap-southeast.bytepluses.com.evil.example/api/v3", {"authorization": "Bearer test-only"}),
            ("https://voice.ap-southeast-1.bytepluses.com/api/v3", {"Authorization": "Bearer test-only"}),
            ("https://ark.ap-southeast.bytepluses.com/api/v3", {"x-api-key": "test-only"}),
            ("http://operator.las.ap-southeast-1.bytepluses.com/api/v1/submit", {"Authorization": "Bearer test-only"}),
            (
                "https://operator.las.ap-southeast-1.bytepluses.com:8443/api/v1/submit",
                {"Authorization": "Bearer test-only"},
            ),
            (
                "https://operator.las.ap-southeast-1.bytepluses.com.evil.example/api/v1/submit",
                {"Authorization": "Bearer test-only"},
            ),
            ("https://operator.las.ap-southeast-1.bytepluses.com/api/v1/submit", {"X-Api-Key": "test-only"}),
        )
        fake = mock.MagicMock()
        fake.__aenter__.return_value = fake
        fake.request.return_value = Response()
        with mock.patch.object(http_requests, "public_download_session", return_value=fake) as session:
            for url, headers in cases:
                with self.subTest(url=url, headers=list(headers)):
                    with self.assertRaises(aiohttp.InvalidURL):
                        await http_requests.send_http_request("POST", url, headers, {}, 5)
            session.assert_not_called()

    async def test_valid_provider_credentials_and_unsigned_media_keep_working(self):
        cases = [
            (url, {"Authorization": "Bearer test-only"})
            for url in (
                *constants.REGION_BASE_URLS.values(),
                *constants.MEDIAKIT_REGION_BASE_URLS.values(),
                *constants.LAS_REGION_BASE_URLS.values(),
                constants.OMNIHUMAN_BASE_URL,
            )
        ]
        cases.extend((url, {"X-Api-Key": "test-only"}) for url in constants.SPEECH_REGION_BASE_URLS.values())
        cases.append(("https://cdn.example/media?signature=test-only", {}))
        for url, headers in cases:
            with self.subTest(url=url):
                session = mock.MagicMock()
                session.__aenter__.return_value = session
                session.request.return_value = Response(body=b"ok")
                with mock.patch.object(http_requests, "public_download_session", return_value=session):
                    status, _, body = await http_requests.send_http_request("GET", url, headers, None, 5)
                self.assertEqual((status, body), (200, b"ok"))
                self.assertEqual(session.request.call_args.args[1], url)

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


@requires_comfyui
class CredentialValidationSecurityTests(unittest.TestCase):
    def test_only_fixed_modelark_endpoints_are_checked(self):
        import requests

        with (
            mock.patch.object(requests, "get", return_value=types.SimpleNamespace(status_code=200)) as legacy,
            mock.patch.object(http_requests, "send_http_request", return_value=(200, {}, b"")) as send,
        ):
            for url in (
                "https://evil.example",
                "http://127.0.0.1",
                constants.REGION_BASE_URLS["ap-southeast-1"] + "/other",
            ):
                with self.subTest(url=url):
                    self.assertIsNone(nodes_shared.check_api_key("test-only", url))
            legacy.assert_not_called()
            send.assert_not_called()

    def test_malformed_keys_are_not_sent_or_logged(self):
        import requests

        with (
            mock.patch.object(requests, "get", return_value=types.SimpleNamespace(status_code=200)) as legacy,
            mock.patch.object(http_requests, "send_http_request", return_value=(200, {}, b"")) as send,
        ):
            for key in ("", None, "test-only\nkey", "test-only\rkey", "test-only\x00key", "test-only key"):
                with self.subTest(key=repr(key)):
                    self.assertIsNone(nodes_shared.check_api_key(key, constants.REGION_BASE_URLS["ap-southeast-1"]))
            legacy.assert_not_called()
            send.assert_not_called()

    def test_key_validation_errors_do_not_echo_credentials(self):
        import requests

        secret = "test-only-sensitive-key"
        with (
            mock.patch.object(requests, "get", side_effect=ValueError(secret)),
            mock.patch.object(http_requests, "send_http_request", side_effect=aiohttp.ClientError(secret)),
            self.assertLogs("BytePlus", level="ERROR") as captured,
        ):
            self.assertIsNone(nodes_shared.check_api_key(secret, constants.REGION_BASE_URLS["ap-southeast-1"]))
        self.assertNotIn(secret, "\n".join(captured.output))

    def test_a_redirect_does_not_validate_a_key_or_reach_its_destination(self):
        import requests

        with (
            mock.patch.object(requests, "get", return_value=types.SimpleNamespace(status_code=302)),
            mock.patch.object(
                http_requests, "send_http_request", return_value=(302, {"Location": "http://127.0.0.1/private"}, b"")
            ) as send,
        ):
            self.assertIsNone(nodes_shared.check_api_key("test-only", constants.REGION_BASE_URLS["ap-southeast-1"]))
        send.assert_called_once()


@requires_comfyui
class ReferenceUrlSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_reference_urls_are_validated_even_when_the_extension_or_slot_gives_the_type(self):
        urls = (
            "https://user:test-only@example.com/x.png",
            "https://127.0.0.1/x.png",
            "https://localhost/x.png",
            "https://example.com:invalid/x.png",
            "https://[invalid/x.png",
        )
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(nodes_shared.BytePlusException):
                    await core_style.resolve_reference_values(None, [url])
                with self.assertRaises(nodes_shared.BytePlusException):
                    await core_style.resolve_typed_reference(None, url, "image")

    async def test_probes_reject_userinfo_and_malformed_destinations_without_requesting_them(self):
        for probe in (core_style._probe_url_kind, core_style.sniff_audio_format):
            for url in ("https://user:test-only@example.com/x", "https://example.com:invalid/x", "https://[invalid/x"):
                session = mock.MagicMock()
                session.__aenter__.return_value = session
                session.request.return_value = Response()
                session.get.return_value = Response()
                with (
                    self.subTest(probe=probe.__name__, url=url),
                    mock.patch.object(core_style, "_public_probe_session", return_value=session),
                    mock.patch.object(core_style, "_host_is_public", mock.AsyncMock(return_value=True)),
                ):
                    self.assertIsNone(await probe(url))
                session.request.assert_not_called()
                session.get.assert_not_called()

    async def test_probe_redirects_cannot_introduce_credentials_or_invalid_urls(self):
        start = "https://cdn.example/start"
        for probe in (core_style._probe_url_kind, core_style.sniff_audio_format):
            for location in (
                "https://user:test-only@example.com/x",
                "https://example.com:invalid/x",
                "https://[invalid/x",
            ):
                session = mock.MagicMock()
                session.__aenter__.return_value = session
                session.request.return_value = Response(302, location)
                session.get.return_value = Response(302, location)
                with (
                    self.subTest(probe=probe.__name__, location=location),
                    mock.patch.object(core_style, "_public_probe_session", return_value=session),
                    mock.patch.object(core_style, "_host_is_public", mock.AsyncMock(return_value=True)),
                ):
                    self.assertIsNone(await probe(start))
                for request in (*session.request.call_args_list, *session.get.call_args_list):
                    self.assertIn(start, request.args)
                    self.assertNotIn(location, request.args)
