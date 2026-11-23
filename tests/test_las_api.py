import asyncio
import copy
import importlib
import json
import os
import sys
import types
import unittest
from unittest import mock

COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
requires_comfyui = unittest.skipUnless(COMFY_ROOT, "Set COMFYUI_ROOT to run LAS API tests.")

if COMFY_ROOT:
    if COMFY_ROOT not in sys.path:
        sys.path.insert(0, COMFY_ROOT)
    importlib.import_module("utils")
    import comfy.cli_args

    comfy.cli_args.args.cpu = True
    PACKAGE_NAME = "byteplus_plugin_test"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [PLUGIN_ROOT]
        sys.modules[PACKAGE_NAME] = package

    import comfy.model_management

    las_api = importlib.import_module(f"{PACKAGE_NAME}.nodes.las_api")
    credentials = importlib.import_module(f"{PACKAGE_NAME}.nodes.credentials")


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())
        patch = mock.patch.dict(os.environ)
        patch.start()
        os.environ.pop("BYTEPLUS_LAS_API_KEY", None)
        os.environ.pop("LAS_API_KEY", None)
        unittest.addModuleCleanup(patch.stop)


GENJUTSU = "las_video_edit_enhance"
TRANSLATION = "las_video_translate"


def envelope(operator_id=GENJUTSU):
    return {
        "operator_id": operator_id,
        "operator_version": "v1",
        "data": {"video_url": "tos://example/source.mp4", "user_prompt": "Improve clarity"},
        "callback": {"url": "https://callback.example/finished"},
    }


def response(status="PENDING", operator_id=GENJUTSU, task_id="task-test-1", code="0"):
    result = {
        "metadata": {
            "task_id": task_id,
            "task_status": status,
            "operator_id": operator_id,
            "operator_version": "v1",
            "business_code": code,
            "error_msg": "",
        }
    }
    if status == "COMPLETED":
        if operator_id == GENJUTSU:
            result["data"] = {
                "id": "generation-test-1",
                "status": "succeeded",
                "content": {"video_url": "https://media.example/result.mp4?signature=test-signature"},
            }
        else:
            result["data"] = {
                "translated_videos": [
                    {
                        "language": "es-MX",
                        "type": "video",
                        "url": "https://media.example/es.mp4?signature=test-signature",
                    }
                ],
                "translated_captions": [],
            }
    return result


class FakeLAS:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def __call__(self, client, method, path, body=None, timeout_seconds=None):
        self.calls.append((method, path, copy.deepcopy(body), timeout_seconds))
        if not self.replies:
            raise AssertionError("Unexpected extra LAS request")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


@requires_comfyui
class LASClientTests(unittest.TestCase):
    def test_client_uses_fixed_region_and_hides_key(self):
        client = las_api.LASClient("test-las-key")
        self.assertEqual(client.region, "ap-southeast-1")
        self.assertEqual(client.base_url, "https://operator.las.ap-southeast-1.bytepluses.com")
        self.assertNotIn("test-las-key", repr(client))
        with self.assertRaises(las_api.BytePlusException):
            las_api.LASClient("test-las-key", "eu-west-1")

    def test_factory_uses_only_las_key_and_refreshes(self):
        with mock.patch.dict(os.environ, {"BYTEPLUS_API_KEY": "test-modelark-key", "BYTEPLUS_REGION": "eu-west-1"}):
            with self.assertRaises(las_api.BytePlusException), mock.patch.object(las_api, "_send") as send:
                las_api.get_las_client()
            send.assert_not_called()
            with mock.patch.dict(os.environ, {"BYTEPLUS_LAS_API_KEY": "test-las-key"}):
                first = las_api.get_las_client()
            with mock.patch.dict(os.environ, {"BYTEPLUS_LAS_API_KEY": "test-changed-key"}):
                second = las_api.get_las_client()
        self.assertEqual(first.api_key, "test-las-key")
        self.assertEqual(second.api_key, "test-changed-key")
        self.assertEqual(second.region, "ap-southeast-1")

    def test_factory_accepts_las_api_key_alias(self):
        with mock.patch.dict(os.environ, {"LAS_API_KEY": "test-alias-key"}, clear=False):
            self.assertEqual(las_api.get_las_client().api_key, "test-alias-key")

    def test_invalid_keys_cannot_be_used_as_http_headers(self):
        for api_key in (None, "", "   ", "test-key\r\nAuthorization: test", "test-key\x00"):
            with self.subTest(api_key=api_key), self.assertRaises(las_api.BytePlusException):
                las_api.LASClient(api_key)

    def test_las_message_keys_are_registered_and_prefixed(self):
        parameters = {
            "path": "test/path",
            "region": "test-region",
            "seconds": 60,
            "e": "test-network-error",
            "status": "FAILED",
            "code": "test-code",
            "message": "test-message",
            "detail": "test-detail",
            "task_id": "task-test-1",
            "elapsed": 1,
        }
        for key in (
            "err_no_las_key",
            "err_las_region",
            "err_las_timeout",
            "err_las_network",
            "err_las_api",
            "err_las_protocol",
            "err_las_task_failed",
            "err_las_poll_failed",
            "err_las_deadline",
            "err_las_resume_changed",
            "err_las_submit_unknown",
            "las_task_submitted",
            "las_task_resuming",
            "las_task_waiting",
        ):
            with self.subTest(key=key):
                message = las_api.get_text(key, **parameters)
                self.assertTrue(message.startswith("[BytePlus] "), message)
                self.assertNotIn("{", message)


@requires_comfyui
class LASTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = las_api.LASClient("test-las-key")
        self.patches = [
            mock.patch.object(las_api, "send_node_text"),
            mock.patch.object(las_api, "LAS_POLL_SECONDS", 0),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        las_api.LAS_TASK_HANDLES.clear()
        las_api.LAS_TASK_LOCKS.clear()
        las_api._TASK_HANDLES_LOADED = False
        try:
            os.remove(las_api._task_handles_path())
        except FileNotFoundError:
            pass
        self.addCleanup(las_api.LAS_TASK_HANDLES.clear)

    async def test_send_sets_bearer_key_and_json_content_type(self):
        with mock.patch.object(
            las_api, "send_http_request", mock.AsyncMock(return_value=(200, {}, json.dumps(response()).encode()))
        ) as send:
            status, body = await las_api._send(self.client, "POST", "/api/v1/submit", envelope(), 7)
        self.assertEqual((status, body), (200, response()))
        args = send.call_args.args
        self.assertEqual(args[:2], ("POST", self.client.base_url + "/api/v1/submit"))
        self.assertEqual(args[2], {"Authorization": "Bearer test-las-key", "Content-Type": "application/json"})
        self.assertEqual(args[3:], (envelope(), 7))

    async def test_send_returns_none_for_bad_json(self):
        with mock.patch.object(las_api, "send_http_request", mock.AsyncMock(return_value=(200, {}, b"not json"))):
            self.assertEqual(await las_api._send(self.client, "POST", "/api/v1/poll", {}), (200, None))

    async def test_submit_preserves_full_envelope_and_does_not_mutate_it(self):
        body = envelope()
        before = copy.deepcopy(body)
        fake = FakeLAS((200, response()))
        with mock.patch.object(las_api, "_send", fake):
            accepted = await las_api.submit_las_task(self.client, body)
        self.assertEqual(accepted["metadata"]["task_id"], "task-test-1")
        self.assertEqual(fake.calls[0][:3], ("POST", "/api/v1/submit", body))
        self.assertEqual(body, before)

    async def test_submit_never_retries_timeout_throttling_or_server_error(self):
        import aiohttp

        for reply in (
            asyncio.TimeoutError(),
            aiohttp.ClientConnectionError("connection lost"),
            (429, {"metadata": {"business_code": "Throttled", "error_msg": "slow down"}}),
            (503, None),
            (400, {"metadata": {"business_code": "InvalidParameter"}}),
        ):
            with self.subTest(reply=type(reply).__name__):
                fake = FakeLAS(reply)
                with mock.patch.object(las_api, "_send", fake), self.assertRaises(las_api.BytePlusException):
                    await las_api.submit_las_task(self.client, envelope())
                self.assertEqual(len(fake.calls), 1)

    async def test_invalid_envelopes_and_poll_inputs_stop_before_send(self):
        for body in (
            None,
            {},
            {**envelope(), "operator_id": "unknown"},
            {**envelope(), "operator_version": "v2"},
            {**envelope(), "data": None},
            {**envelope(), "data": {"number": float("nan")}},
            {**envelope(), "task_id": "unexpected"},
        ):
            with self.subTest(body=body):
                with mock.patch.object(las_api, "_send", mock.AsyncMock()) as send:
                    with self.assertRaises(las_api.BytePlusException):
                        await las_api.submit_las_task(self.client, body)
                    send.assert_not_called()
        for task_id in ("", None, 123, "task\nbad", "task\x00bad"):
            with mock.patch.object(las_api, "_send", mock.AsyncMock()) as send:
                with self.assertRaises(las_api.BytePlusException):
                    await las_api.poll_las_task(self.client, GENJUTSU, task_id)
                send.assert_not_called()

    async def test_missing_metadata_id_status_code_and_unknown_states_fail(self):
        variants = [None, [], {}, {"metadata": []}]
        for field in ("task_id", "task_status", "business_code"):
            item = response()
            item["metadata"].pop(field)
            variants.append(item)
        for status in ("", "queued", "UNKNOWN", None, []):
            variants.append(response(status=status))
        for code in (None, False, 0.0, [], {}):
            variants.append(response(code=code))
        for body in variants:
            with self.subTest(body=body):
                with mock.patch.object(las_api, "_send", FakeLAS((200, body))):
                    with self.assertRaises(las_api.BytePlusException):
                        await las_api.submit_las_task(self.client, envelope())

    async def test_business_failure_at_http_200_is_not_retryable(self):
        fake = FakeLAS((200, response(code="Task.NotFound")))
        with mock.patch.object(las_api, "_send", fake), self.assertRaises(las_api.BytePlusException) as caught:
            await las_api.wait_for_las_task(self.client, GENJUTSU, "task-test-1")
        self.assertIn("Task.NotFound", str(caught.exception))
        self.assertEqual(len(fake.calls), 1)

    async def test_poll_envelope_and_optional_identity_echoes(self):
        done = response("COMPLETED")
        done["metadata"].pop("task_id")
        fake = FakeLAS((200, done))
        with mock.patch.object(las_api, "_send", fake):
            collected = await las_api.poll_las_task(self.client, GENJUTSU, "task-test-1")
        self.assertEqual(collected["metadata"]["task_id"], "task-test-1")
        self.assertEqual(collected["data"], done["data"])
        self.assertNotIn("task_id", done["metadata"])
        self.assertEqual(
            fake.calls[0][:3],
            (
                "POST",
                "/api/v1/poll",
                {"operator_id": GENJUTSU, "operator_version": "v1", "task_id": "task-test-1"},
            ),
        )

    async def test_poll_mismatched_identity_fails_without_retry(self):
        for field, wrong in (("task_id", "another-task"), ("operator_id", TRANSLATION), ("operator_version", "v2")):
            done = response("COMPLETED")
            done["metadata"][field] = wrong
            fake = FakeLAS((200, done))
            with mock.patch.object(las_api, "_send", fake), self.assertRaises(las_api.BytePlusException):
                await las_api.wait_for_las_task(self.client, GENJUTSU, "task-test-1")
            self.assertEqual(len(fake.calls), 1)

    async def test_pending_running_completed_wait_and_readonly_poll(self):
        fake = FakeLAS((200, response()), (200, response("RUNNING")), (200, response("COMPLETED")))
        with mock.patch.object(las_api, "_send", fake):
            result = await las_api.wait_for_las_task(self.client, GENJUTSU, "task-test-1", "node-one")
        self.assertEqual(result["data"]["status"], "succeeded")
        self.assertEqual([call[1] for call in fake.calls], ["/api/v1/poll"] * 3)
        with mock.patch.object(las_api, "_send", FakeLAS((200, response()))):
            self.assertEqual(
                (await las_api.poll_las_task(self.client, GENJUTSU, "task-test-1"))["metadata"]["task_status"],
                "PENDING",
            )

    async def test_failed_timeout_and_nested_genjutsu_failure_stop(self):
        variants = [response("FAILED"), response("TIMEOUT")]
        for nested_status in ("failed", "expired", "running", "unknown", None):
            item = response("COMPLETED")
            item["data"]["status"] = nested_status
            variants.append(item)
        pending_failure = response("RUNNING")
        pending_failure["data"] = {"status": "failed", "error": {"code": "InvalidVideo", "message": "decode failed"}}
        variants.append(pending_failure)
        for item in variants:
            with self.subTest(item=item):
                fake = FakeLAS((200, item))
                with mock.patch.object(las_api, "_send", fake), self.assertRaises(las_api.BytePlusException):
                    await las_api.wait_for_las_task(self.client, GENJUTSU, "task-test-1")
                self.assertEqual(len(fake.calls), 1)

    async def test_completed_operator_result_shapes_are_required(self):
        for operator in (GENJUTSU, TRANSLATION):
            done = response("COMPLETED", operator)
            for data in (None, {}, [], {"content": {}}, {"translated_videos": "invalid"}):
                with mock.patch.object(las_api, "_send", FakeLAS((200, {**done, "data": data}))):
                    with self.assertRaises(las_api.BytePlusException):
                        await las_api.poll_las_task(self.client, operator, "task-test-1")
            with mock.patch.object(las_api, "_send", FakeLAS((200, done))):
                self.assertEqual(await las_api.poll_las_task(self.client, operator, "task-test-1"), done)

    async def test_transient_polls_recover_and_reset_consecutive_failure_budget(self):
        import aiohttp

        transient = [
            asyncio.TimeoutError(),
            aiohttp.ClientConnectionError("lost"),
            (429, None),
            (502, None),
        ]
        fake = FakeLAS(*transient, (200, response("RUNNING")), *transient, (200, response("COMPLETED")))
        with mock.patch.object(las_api, "_send", fake):
            self.assertEqual(
                (await las_api.wait_for_las_task(self.client, GENJUTSU, "task-test-1"))["metadata"]["task_status"],
                "COMPLETED",
            )
        self.assertEqual(len(fake.calls), 10)

    async def test_fifth_failed_poll_and_permanent_http_errors_stop(self):
        fake = FakeLAS(*[(503, None)] * 5)
        with mock.patch.object(las_api, "_send", fake), self.assertRaises(las_api.BytePlusException) as caught:
            await las_api.wait_for_las_task(self.client, GENJUTSU, "task-test-1")
        self.assertIn("task-test-1", str(caught.exception))
        self.assertEqual(len(fake.calls), 5)
        for status in (400, 401, 403, 404, 408, 409):
            fake = FakeLAS((status, {"metadata": {"business_code": "Task.NotFound"}}))
            with mock.patch.object(las_api, "_send", fake), self.assertRaises(las_api.BytePlusException):
                await las_api.wait_for_las_task(self.client, GENJUTSU, "task-test-1")
            self.assertEqual(len(fake.calls), 1)

    async def test_expired_deadline_prevents_poll_and_inflight_poll_is_bounded(self):
        with mock.patch.object(las_api, "_send", mock.AsyncMock()) as send:
            with self.assertRaises(las_api.BytePlusException):
                await las_api.wait_for_las_task(self.client, GENJUTSU, "task-test-1", deadline=0)
            send.assert_not_called()
        stopped = asyncio.Event()

        async def slow(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        with mock.patch.object(las_api, "_send", slow), self.assertRaises(las_api.BytePlusException):
            await las_api.wait_for_las_task(self.client, GENJUTSU, "task-test-1", deadline=las_api.time.time() + 0.03)
        await asyncio.wait_for(stopped.wait(), 1)

    async def test_invalid_path_does_not_forward_credentials(self):
        for path in ("https://media.example/result", "//media.example/result", "/api/v1/submit?key=test"):
            with mock.patch.object(las_api, "_send", mock.AsyncMock()) as send:
                with self.assertRaises(las_api.BytePlusException):
                    await las_api.las_request(self.client, path, {})
                send.assert_not_called()

    async def test_errors_redact_echoed_keys_and_signed_urls_without_raw_cause(self):
        import aiohttp

        echoed = "test-las-key https://user:password@media.example/result.mp4?signature=test-signature#fragment"
        for reply in (
            (400, {"metadata": {"business_code": "InvalidVideo", "error_msg": echoed}}),
            (200, response(code=echoed)),
            aiohttp.ClientConnectionError(echoed),
        ):
            with (
                mock.patch.object(las_api, "_send", FakeLAS(reply)),
                self.assertRaises(las_api.BytePlusException) as caught,
            ):
                await las_api.submit_las_task(self.client, envelope())
            error = str(caught.exception)
            for secret in ("test-las-key", "test-signature", "password", "fragment"):
                self.assertNotIn(secret, error)
            self.assertIsNone(caught.exception.__cause__)
            self.assertTrue(caught.exception.__suppress_context__)

    def test_redact_response_recursively_without_changing_download_urls(self):
        original = {
            "api_key": "test-las-key",
            "Authorization": "Bearer other-test-key",
            "nested": [
                {
                    "url": "https://user:password@media.example/video.mp4?X-Tos-Signature=test-signature#fragment",
                    "message": "echo test-las-key, https://media.example/audio.wav?signature=another-test-signature",
                    "accessKey": "other-test-secret",
                    "token": "test-token",
                    "tos_path": "tos://example/output/",
                }
            ],
            "metadata": {"task_id": "task-test-1"},
        }
        before = copy.deepcopy(original)
        sanitized = las_api.redact_las_response(original, self.client.api_key)
        rendered = json.dumps(sanitized)
        for secret in ("test-las-key", "test-signature", "other-test-secret", "test-token", "password", "fragment"):
            self.assertNotIn(secret, rendered)
        self.assertEqual(sanitized["nested"][0]["url"], "https://media.example/video.mp4")
        self.assertEqual(sanitized["metadata"]["task_id"], "task-test-1")
        self.assertEqual(sanitized["nested"][0]["tos_path"], "tos://example/output/")
        self.assertEqual(original, before)

    def test_redaction_accepts_multiple_explicit_secrets_and_current_default_key(self):
        raw = {"message": "test-active-key test-old-key test-injected-key"}
        with mock.patch.dict(os.environ, {"BYTEPLUS_LAS_API_KEY": "test-active-key"}):
            self.assertEqual(
                las_api.redact_las_response(raw, secrets=("test-old-key", "test-injected-key")),
                {"message": "[redacted] [redacted] [redacted]"},
            )
        raw = {"message": "test/key test%2Fkey"}
        self.assertEqual(
            las_api.redact_las_response(raw, secrets=("test/key",)),
            {"message": "[redacted] [redacted]"},
        )


@requires_comfyui
class LASRecoveryTests(unittest.IsolatedAsyncioTestCase):
    setUp = LASTransportTests.setUp

    async def test_collection_does_not_clear_handle_while_same_node_callers_are_waiting(self):
        body = envelope()
        with mock.patch.object(las_api, "_send", FakeLAS((200, response("COMPLETED")))):
            await las_api.submit_and_wait(self.client, body, "node-one")
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def fake(client, method, path, body=None, timeout_seconds=None):
            calls.append(path)
            started.set()
            await release.wait()
            return 200, response("COMPLETED")

        with mock.patch.object(las_api, "_send", fake):
            collecting = asyncio.create_task(las_api.submit_and_wait(self.client, body, "node-one"))
            await started.wait()
            queued = asyncio.create_task(las_api.submit_and_wait(self.client, body, "node-one"))
            await asyncio.sleep(0)
            self.assertFalse(las_api.clear_las_task_handle(self.client, body, "node-one"))
            release.set()
            await asyncio.gather(collecting, queued)
        self.assertEqual(calls, ["/api/v1/poll", "/api/v1/poll"])
        self.assertTrue(las_api.clear_las_task_handle(self.client, body, "node-one"))

    async def test_accepted_handle_survives_interrupt_and_same_request_resumes(self):
        body = envelope()
        fake = FakeLAS((200, response()), comfy.model_management.InterruptProcessingException())
        with mock.patch.object(las_api, "_send", fake):
            with self.assertRaises(comfy.model_management.InterruptProcessingException):
                await las_api.submit_and_wait(self.client, body, "node-one")
        self.assertEqual(len(las_api.LAS_TASK_HANDLES), 1)
        resumed = FakeLAS((200, response("COMPLETED")))
        with mock.patch.object(las_api, "_send", resumed):
            await las_api.submit_and_wait(self.client, body, "node-one")
        self.assertEqual([call[1] for call in resumed.calls], ["/api/v1/poll"])
        self.assertEqual(len(las_api.LAS_TASK_HANDLES), 1)
        self.assertEqual(las_api.LAS_TASK_LOCKS, {})

    async def test_persisted_handle_reloads_after_memory_is_cleared(self):
        body = envelope()
        fake = FakeLAS((200, response()), comfy.model_management.InterruptProcessingException())
        with mock.patch.object(las_api, "_send", fake):
            with self.assertRaises(comfy.model_management.InterruptProcessingException):
                await las_api.submit_and_wait(self.client, body, "node-one")
        self.assertTrue(os.path.exists(las_api._task_handles_path()))
        las_api.LAS_TASK_HANDLES.clear()
        resumed = FakeLAS((200, response("COMPLETED")))
        with mock.patch.object(las_api, "_send", resumed):
            await las_api.submit_and_wait(self.client, body, "node-one")
        self.assertEqual([call[1] for call in resumed.calls], ["/api/v1/poll"])

    async def test_accepted_handle_survives_permanent_poll_failure_and_deadline(self):
        body = envelope()
        fake = FakeLAS((200, response()), (401, {"metadata": {"business_code": "Unauthorized"}}))
        with mock.patch.object(las_api, "_send", fake), self.assertRaises(las_api.BytePlusException):
            await las_api.submit_and_wait(self.client, body, "node-one")
        self.assertEqual(len(las_api.LAS_TASK_HANDLES), 1)
        with (
            mock.patch.object(las_api, "LAS_TASK_RETENTION_SECONDS", 0),
            mock.patch.object(las_api, "_send", mock.AsyncMock()) as send,
        ):
            with self.assertRaises(las_api.BytePlusException):
                await las_api.submit_and_wait(self.client, body, "node-one")
            send.assert_not_called()
        self.assertEqual(len(las_api.LAS_TASK_HANDLES), 1)

    async def test_accepted_id_is_retained_even_when_submit_reports_terminal_failure(self):
        for item in (response("FAILED"), response("COMPLETED", code="InvalidVideo"), response(status="UNKNOWN")):
            las_api.LAS_TASK_HANDLES.clear()
            with mock.patch.object(las_api, "_send", FakeLAS((200, item))):
                with self.assertRaises(las_api.BytePlusException):
                    await las_api.submit_and_wait(self.client, envelope(), "node-one")
            self.assertEqual(len(las_api.LAS_TASK_HANDLES), 1)

    async def test_changed_request_credential_and_seed_do_not_attach_or_resubmit(self):
        body = envelope()
        fake = FakeLAS((200, response()), (503, None), (503, None), (503, None), (503, None), (503, None))
        with mock.patch.object(las_api, "_send", fake), self.assertRaises(las_api.BytePlusException):
            await las_api.submit_and_wait(self.client, body, "node-one")
        changed_body = copy.deepcopy(body)
        changed_body["data"]["user_prompt"] = "Replace background"
        for client, request, seed in (
            (self.client, changed_body, 0),
            (las_api.LASClient("test-changed-key"), body, 0),
            (self.client, body, 1),
            (self.client, envelope(TRANSLATION), 0),
        ):
            with mock.patch.object(las_api, "_send", mock.AsyncMock()) as send:
                with self.assertRaises(las_api.BytePlusException) as caught:
                    await las_api.submit_and_wait(client, request, "node-one", seed)
                self.assertIn("task-test-1", str(caught.exception))
                send.assert_not_called()

    async def test_handle_contains_hashes_and_no_raw_request_or_credentials(self):
        body = envelope()
        body["data"]["video_url"] = "https://media.example/source.mp4?signature=test-private-signature"
        with mock.patch.object(las_api, "_send", FakeLAS((200, response("COMPLETED")))):
            await las_api.submit_and_wait(self.client, body, "node-one")
        rendered = repr(las_api.LAS_TASK_HANDLES)
        for private in (
            "test-las-key",
            "test-private-signature",
            "Improve clarity",
            "media.example",
            "callback.example",
        ):
            self.assertNotIn(private, rendered)
        reordered = {key: body[key] for key in reversed(body)}
        with mock.patch.object(las_api, "_send", FakeLAS((200, response("COMPLETED")))) as send:
            await las_api.submit_and_wait(self.client, reordered, "node-one")
        self.assertEqual([call[1] for call in send.calls], ["/api/v1/poll"])

    async def test_completed_handle_is_only_cleared_explicitly_after_collection(self):
        body = envelope()
        with mock.patch.object(las_api, "_send", FakeLAS((200, response("COMPLETED")))):
            await las_api.submit_and_wait(self.client, body, "node-one")
        self.assertEqual(len(las_api.LAS_TASK_HANDLES), 1)
        self.assertFalse(las_api.clear_las_task_handle(self.client, body, "node-one", 1))
        self.assertFalse(las_api.clear_las_task_handle(las_api.LASClient("test-changed-key"), body, "node-one"))
        self.assertTrue(las_api.clear_las_task_handle(self.client, body, "node-one"))
        self.assertFalse(las_api.clear_las_task_handle(self.client, body, "node-one"))
        fake = FakeLAS((200, response("COMPLETED", task_id="task-test-2")))
        with mock.patch.object(las_api, "_send", fake):
            await las_api.submit_and_wait(self.client, body, "node-one", 1)
        self.assertEqual(fake.calls[0][1], "/api/v1/submit")

    async def test_concurrent_same_node_submits_once_but_independent_nodes_submit_separately(self):
        calls = []

        async def fake(client, method, path, body=None, timeout_seconds=None):
            calls.append((path, copy.deepcopy(body)))
            await asyncio.sleep(0)
            return 200, response("COMPLETED")

        with mock.patch.object(las_api, "_send", fake):
            results = await asyncio.gather(
                las_api.submit_and_wait(self.client, envelope(), "node-one"),
                las_api.submit_and_wait(self.client, envelope(), "node-one"),
                las_api.submit_and_wait(self.client, envelope(), "node-two"),
            )
        self.assertEqual(len(results), 3)
        self.assertEqual([path for path, _ in calls].count("/api/v1/submit"), 2)
        self.assertEqual(las_api.LAS_TASK_LOCKS, {})

    async def test_concurrent_changed_key_does_not_submit_a_second_task(self):
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def fake(client, method, path, body=None, timeout_seconds=None):
            calls.append(path)
            started.set()
            await release.wait()
            return 200, response("COMPLETED")

        with mock.patch.object(las_api, "_send", fake):
            first = asyncio.create_task(las_api.submit_and_wait(self.client, envelope(), "node-one"))
            await started.wait()
            second = asyncio.create_task(
                las_api.submit_and_wait(las_api.LASClient("test-changed-key"), envelope(), "node-one")
            )
            release.set()
            await first
            with self.assertRaises(las_api.BytePlusException):
                await second
        self.assertEqual(calls, ["/api/v1/submit"])
        self.assertEqual(las_api.LAS_TASK_LOCKS, {})

    async def test_cancellation_during_poll_retains_handle_and_releases_lock(self):
        started = asyncio.Event()

        async def fake(client, method, path, body=None, timeout_seconds=None):
            if path == "/api/v1/submit":
                return 200, response()
            started.set()
            await asyncio.Event().wait()

        with mock.patch.object(las_api, "_send", fake):
            running = asyncio.create_task(las_api.submit_and_wait(self.client, envelope(), "node-one"))
            await started.wait()
            running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running
        self.assertEqual(len(las_api.LAS_TASK_HANDLES), 1)
        self.assertEqual(las_api.LAS_TASK_LOCKS, {})

    async def test_cancelled_lock_waiter_does_not_submit_or_remove_accepted_handle(self):
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def fake(client, method, path, body=None, timeout_seconds=None):
            calls.append(path)
            started.set()
            await release.wait()
            return 200, response("COMPLETED")

        with mock.patch.object(las_api, "_send", fake):
            owner = asyncio.create_task(las_api.submit_and_wait(self.client, envelope(), "node-one"))
            await started.wait()
            waiter = asyncio.create_task(las_api.submit_and_wait(self.client, envelope(), "node-one"))
            await asyncio.sleep(0)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            release.set()
            await owner
        self.assertEqual(calls, ["/api/v1/submit"])
        self.assertEqual(len(las_api.LAS_TASK_HANDLES), 1)
        self.assertEqual(las_api.LAS_TASK_LOCKS, {})

    async def test_preacceptance_interrupt_reports_ambiguity_without_inventing_handle(self):
        fake = FakeLAS(comfy.model_management.InterruptProcessingException())
        with mock.patch.object(las_api, "_send", fake):
            with self.assertRaises(comfy.model_management.InterruptProcessingException):
                await las_api.submit_and_wait(self.client, envelope(), "node-one")
        self.assertEqual(las_api.LAS_TASK_HANDLES, {})
        self.assertEqual(len(fake.calls), 1)
        self.assertTrue(las_api.send_node_text.called)


if __name__ == "__main__":
    unittest.main()
