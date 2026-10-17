"""
The request helpers Seed Speech and AI MediaKit share (nodes/http_requests.py).

  COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_http_requests
"""

import asyncio
import importlib
import os
import sys
import types
import unittest
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
    http_requests = importlib.import_module(f"{PACKAGE_NAME}.nodes.http_requests")


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


@requires_comfyui
class AwaitRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_the_result(self):
        async def send():
            return "ok"

        self.assertEqual(await http_requests.await_request(send(), "slow", str), "ok")

    async def test_timeouts_are_retryable_with_the_given_text(self):
        async def send():
            raise asyncio.TimeoutError()

        with self.assertRaises(http_requests.RetryableRequestError) as ctx:
            await http_requests.await_request(send(), "took too long", str)
        self.assertTrue(ctx.exception.retryable)
        self.assertIn("took too long", str(ctx.exception))

    async def test_network_errors_are_retryable_and_use_the_error_text(self):
        import aiohttp

        async def send():
            raise aiohttp.ClientConnectionError("reset")

        with self.assertRaises(http_requests.RetryableRequestError) as ctx:
            await http_requests.await_request(send(), "slow", lambda e: f"network: {e}")
        self.assertTrue(ctx.exception.retryable)
        self.assertIn("network: reset", str(ctx.exception))

    async def test_other_errors_pass_through(self):
        async def send():
            raise ValueError("bug")

        with self.assertRaises(ValueError):
            await http_requests.await_request(send(), "slow", str)


@requires_comfyui
class RetryTransientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.object(http_requests, "sleep_interruptible", mock.AsyncMock())
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_retryable_statuses(self):
        for status, expected in ((200, False), (400, False), (404, False), (429, True), (500, True), (503, True)):
            with self.subTest(status=status):
                self.assertEqual(http_requests.is_retryable_status(status), expected)

    async def test_transient_failures_are_retried_then_succeed(self):
        calls = []

        async def request():
            calls.append(1)
            if len(calls) < 3:
                raise http_requests.RetryableRequestError("busy", retryable=True)
            return "done"

        self.assertEqual(await http_requests.retry_transient(request, max_errors=5, poll_seconds=1), "done")
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.sleep.await_count, 2)

    async def test_permanent_failures_are_raised_at_once(self):
        calls = []

        async def request():
            calls.append(1)
            raise http_requests.RetryableRequestError("bad key", retryable=False, status=401)

        with self.assertRaises(http_requests.RetryableRequestError) as ctx:
            await http_requests.retry_transient(request, max_errors=5, poll_seconds=1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(ctx.exception.status, 401)

    async def test_gives_up_after_max_errors_in_a_row(self):
        calls = []

        async def request():
            calls.append(1)
            raise http_requests.RetryableRequestError("busy", retryable=True)

        with self.assertRaises(http_requests.RetryableRequestError):
            await http_requests.retry_transient(request, max_errors=4, poll_seconds=1)
        self.assertEqual(len(calls), 4)

    async def test_other_exceptions_are_not_retried(self):
        async def request():
            raise ValueError("bug")

        with self.assertRaises(ValueError):
            await http_requests.retry_transient(request, max_errors=5, poll_seconds=1)
        self.sleep.assert_not_awaited()


@requires_comfyui
class ArkTaskPollTests(unittest.TestCase):
    def setUp(self):
        self.ark_tasks = importlib.import_module(f"{PACKAGE_NAME}.nodes.ark_tasks")

    def test_client_errors_other_than_throttling_are_permanent(self):
        for status, expected in ((401, True), (403, True), (404, True), (408, False), (409, False), (429, False)):
            with self.subTest(status=status):
                error = types.SimpleNamespace(status_code=status)
                self.assertEqual(self.ark_tasks.poll_error_is_permanent(error), expected)

    def test_server_and_network_errors_are_transient(self):
        self.assertFalse(self.ark_tasks.poll_error_is_permanent(types.SimpleNamespace(status_code=503)))
        self.assertFalse(self.ark_tasks.poll_error_is_permanent(ConnectionError("reset")))

    def test_transient_failures_end_the_wait_only_at_the_limit(self):
        error = ConnectionError("reset")
        self.assertFalse(self.ark_tasks.poll_failure_is_final(error, 2, 3))
        self.assertTrue(self.ark_tasks.poll_failure_is_final(error, 3, 3))

    def test_permanent_failures_end_the_wait_at_once(self):
        self.assertTrue(self.ark_tasks.poll_failure_is_final(types.SimpleNamespace(status_code=401), 1, 15))
