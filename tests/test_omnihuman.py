import asyncio
import importlib
import json
import os
import sys
import tempfile
import types
import unittest
import wave
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
requires_comfyui = unittest.skipUnless(COMFY_ROOT, "Set COMFYUI_ROOT to run OmniHuman tests.")

if COMFY_ROOT:
    sys.path.insert(0, COMFY_ROOT)
    importlib.import_module("utils")
    import comfy.cli_args
    import torch

    comfy.cli_args.args.cpu = True
    PACKAGE_NAME = "byteplus_plugin_test"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [PLUGIN_ROOT]
        sys.modules[PACKAGE_NAME] = package
    api = importlib.import_module(f"{PACKAGE_NAME}.nodes.omnihuman_api")
    media = importlib.import_module(f"{PACKAGE_NAME}.nodes.omnihuman_media")
    nodes = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_omnihuman")
    credentials = importlib.import_module(f"{PACKAGE_NAME}.nodes.credentials")
    http_requests = importlib.import_module(f"{PACKAGE_NAME}.nodes.http_requests")
    from comfy_execution.graph_utils import ExecutionBlocker


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


@requires_comfyui
class RequestTests(unittest.TestCase):
    def test_latest_model_and_optional_prompt_follow_official_body(self):
        body = api.build_request("https://example.com/portrait.png", "https://example.com/speech.wav")
        self.assertEqual(
            body,
            {
                "req_key": "realman_avatar_picture_omni20_cv",
                "image_urls": ["https://example.com/portrait.png"],
                "audio_url": "https://example.com/speech.wav",
                "enable_watermark": True,
            },
        )
        body = api.build_request(
            "https://example.com/image", "https://example.com/audio", prompt=" smile ", enable_watermark=False
        )
        self.assertEqual(body["prompt"], "smile")
        self.assertFalse(body["enable_watermark"])

    def test_invalid_urls_and_options_are_rejected_without_echoing_signed_values(self):
        for url in (
            "",
            None,
            [],
            "file:///etc/passwd",
            "http://localhost/x",
            "https://127.0.0.1/x",
            "https://[::1]/x",
            "https://user:private-password@example.com/x",
            "https://example.com/\n",
        ):
            with self.subTest(url=url), self.assertRaises(api.BytePlusException) as caught:
                api.build_request(url, "https://example.com/audio.wav")
            self.assertNotIn("private-password", str(caught.exception))
        for value in (None, 1, "true"):
            with self.assertRaises(api.BytePlusException):
                api.build_request("https://example.com/x", "https://example.com/a", enable_watermark=value)
        with self.assertRaises(api.BytePlusException):
            api.build_request("https://example.com/x", "https://example.com/a", prompt=123)

    def test_client_reads_only_vision_key_and_hides_it(self):
        with mock.patch.object(credentials, "get_setting", return_value="vision-test-value"):
            client = api.get_omnihuman_client()
        self.assertNotIn("vision-test-value", repr(client))
        self.assertEqual(client.base_url, "https://openapi.cv.bytepluses.com")
        with mock.patch.object(credentials, "get_setting", return_value="") as setting:
            with self.assertRaises(api.BytePlusException):
                api.get_omnihuman_client()
        setting.assert_called_once_with("BYTEPLUS_CV_API_KEY")
        for value in ("", None, "key\ninjection"):
            with self.assertRaises(api.BytePlusException):
                api.OmniHumanClient(value)

    def test_response_redacts_credentials_and_signed_urls_recursively(self):
        response = api.redact_response(
            {
                "message": "key=fake-vision-key https://example.com/x?Signature=private",
                "nested": {
                    "Authorization": "Bearer fake-vision-key",
                    "video_url": "https://example.com/v?token=private",
                },
            },
            "fake-vision-key",
        )
        text = json.dumps(response)
        self.assertNotIn("fake-vision-key", text)
        self.assertNotIn("private", text)


@requires_comfyui
class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = api.OmniHumanClient("fake-vision-key")

    async def test_transport_uses_official_bearer_host_and_paths(self):
        with mock.patch.object(api, "send_http_request", return_value=(200, {}, b'{"code":10000,"data":{}}')) as send:
            await api._send(self.client, api.SUBMIT_PATH, {"req_key": api.REQ_KEY})
        arguments = send.call_args.args
        self.assertEqual(arguments[0], "POST")
        self.assertEqual(arguments[1], "https://openapi.cv.bytepluses.com/api/common/v3/submit_task_v2")
        self.assertEqual(arguments[2]["Authorization"], "Bearer fake-vision-key")

    async def test_real_transport_allows_vision_host_and_rejects_untrusted_host_before_connecting(self):
        response = SimpleNamespace(status=200, headers={}, read=mock.AsyncMock(return_value=b'{"code":10000}'))
        response_context = mock.MagicMock()
        response_context.__aenter__.return_value = response
        session = SimpleNamespace(request=mock.Mock(return_value=response_context))
        session_context = mock.MagicMock()
        session_context.__aenter__.return_value = session
        with mock.patch.object(http_requests, "public_download_session", return_value=session_context):
            self.assertEqual(await api._send(self.client, api.RESULT_PATH, {}), (200, {"code": 10000}))
            self.client.base_url = "https://untrusted.example"
            with self.assertRaises(api.aiohttp.InvalidURL):
                await api._send(self.client, api.RESULT_PATH, {})
        self.assertEqual(session.request.call_count, 1)

    async def test_submit_requires_outer_success_code_and_task_id(self):
        for response in (
            {"code": 10000, "data": {}},
            {"code": 50215, "data": {"task_id": "do-not-use"}, "message": "invalid"},
            {"status": 10000, "data": {"task_id": "do-not-use"}},
            {"code": True, "data": {"task_id": "do-not-use"}},
        ):
            with self.subTest(response=response), mock.patch.object(api, "_send", return_value=(200, response)):
                with self.assertRaises(api.BytePlusException):
                    await api.submit_task(self.client, {})
        with mock.patch.object(api, "_send", return_value=(200, {"code": 10000, "data": {"task_id": "test-task"}})):
            self.assertEqual(await api.submit_task(self.client, {}), "test-task")

    async def test_paid_submit_is_never_retried_on_timeout_throttling_or_server_error(self):
        for outcome in (asyncio.TimeoutError(), (429, {"code": 50429}), (500, {"code": 50500})):
            with self.subTest(outcome=outcome):
                send = (
                    mock.AsyncMock(side_effect=outcome)
                    if isinstance(outcome, Exception)
                    else mock.AsyncMock(return_value=outcome)
                )
                with mock.patch.object(api, "_send", send), self.assertRaises(api.BytePlusException):
                    await api.submit_task(self.client, {})
                self.assertEqual(send.await_count, 1)

    async def test_poll_checks_outer_code_before_data_and_retries_only_transient_failures(self):
        responses = [
            (429, {"code": 50429}),
            (200, {"code": 10000, "data": {"status": "in_queue"}}),
            (200, {"code": 10000, "data": {"status": "generating"}}),
            (200, {"code": 10000, "data": {"status": "done", "video_url": "https://example.com/video.mp4"}}),
        ]
        with (
            mock.patch.object(api, "_send", side_effect=responses) as send,
            mock.patch.object(api, "sleep_interruptible"),
        ):
            result = await api.wait_for_task(self.client, "test-task")
        self.assertEqual(result["data"]["status"], "done")
        self.assertEqual(send.await_count, 4)
        with mock.patch.object(api, "_send", return_value=(500, {"code": 50510, "data": {"status": "done"}})) as send:
            with self.assertRaises(api.BytePlusException):
                await api.wait_for_task(self.client, "test-task")
        self.assertEqual(send.await_count, 1)

    async def test_unknown_expired_and_missing_states_fail_with_recovery_task_id(self):
        for state in ("invalid", "expired", "not_found", "canceled"):
            with mock.patch.object(api, "_send", return_value=(200, {"code": 10000, "data": {"status": state}})):
                with self.assertRaises(api.BytePlusException) as caught:
                    await api.wait_for_task(self.client, "test-task")
            self.assertIn("test-task", str(caught.exception))

    async def test_poll_exhaustion_and_interrupt_do_not_resubmit(self):
        with (
            mock.patch.object(api, "_send", return_value=(503, {"code": 50500})) as send,
            mock.patch.object(api, "sleep_interruptible"),
        ):
            with self.assertRaises(api.BytePlusException) as caught:
                await api.wait_for_task(self.client, "test-task")
        self.assertEqual(send.await_count, api.MAX_POLL_ERRORS)
        self.assertIn("test-task", str(caught.exception))
        interruption = comfy.model_management.InterruptProcessingException()
        with mock.patch.object(api, "_send", side_effect=interruption) as send:
            with self.assertRaises(comfy.model_management.InterruptProcessingException):
                await api.wait_for_task(self.client, "test-task")
        self.assertEqual(send.await_count, 1)

    async def test_non_json_transient_poll_errors_are_retried_without_resubmission(self):
        for status in (429, 502, 503):
            with (
                self.subTest(status=status),
                mock.patch.object(
                    api,
                    "_send",
                    side_effect=[
                        (status, None),
                        (200, {"code": 10000, "data": {"status": "done", "video_url": "https://example.com/v"}}),
                    ],
                ) as send,
                mock.patch.object(api, "sleep_interruptible"),
                mock.patch.object(api, "submit_task") as submit,
            ):
                result = await api.wait_for_task(self.client, "test-task")
                self.assertEqual(result["data"]["status"], "done")
                self.assertEqual(send.await_count, 2)
                submit.assert_not_called()

    async def test_invalid_poll_destination_is_permanent_and_not_retried(self):
        with (
            mock.patch.object(api, "_send", side_effect=api.aiohttp.InvalidURL("blocked")) as send,
            mock.patch.object(api, "sleep_interruptible") as sleep,
            self.assertRaises(api.BytePlusException),
        ):
            await api.wait_for_task(self.client, "test-task")
        self.assertEqual(send.await_count, 1)
        sleep.assert_not_called()

    async def test_cancel_reports_already_started_without_claiming_success(self):
        with mock.patch.object(api, "_send", return_value=(200, {"code": 50217, "message": "already executed"})):
            canceled, _ = await api.cancel_task(self.client, "test-task")
        self.assertFalse(canceled)
        with mock.patch.object(api, "_send", return_value=(200, {"code": 10000})):
            canceled, _ = await api.cancel_task(self.client, "test-task")
        self.assertTrue(canceled)

    async def test_cancel_does_not_accept_error_http_status_as_already_started(self):
        with mock.patch.object(api, "_send", return_value=(503, {"code": 50217})):
            with self.assertRaises(api.BytePlusException):
                await api.cancel_task(self.client, "test-task")

    async def test_malformed_poll_payloads_fail_with_safe_recovery_id(self):
        for response in (
            {"code": 10000, "data": []},
            {"code": 10000, "data": {"status": []}},
            {"code": 10000, "data": {"status": "done"}},
            {"code": 10000, "data": {"status": "done", "video_url": "http://127.0.0.1/private"}},
        ):
            with mock.patch.object(api, "_send", return_value=(200, response)):
                with self.assertRaises(api.BytePlusException) as caught:
                    await api.wait_for_task(self.client, "test-task")
                self.assertIn("test-task", str(caught.exception))
                self.assertNotIn("/private", str(caught.exception))


@requires_comfyui
class MediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_video_downloads_through_current_shared_transport(self):
        import av
        import numpy

        buffer = BytesIO()
        with av.open(buffer, mode="w", format="mp4") as container:
            stream = container.add_stream("mpeg4", rate=1)
            stream.width = stream.height = 16
            stream.pix_fmt = "yuv420p"
            frame = av.VideoFrame.from_ndarray(numpy.zeros((16, 16, 3), dtype=numpy.uint8), format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        response = SimpleNamespace(
            status=200,
            headers={},
            content=SimpleNamespace(read=mock.AsyncMock(side_effect=[buffer.getvalue(), b""])),
            raise_for_status=mock.Mock(),
        )
        response_context = mock.MagicMock()
        response_context.__aenter__.return_value = response
        session = SimpleNamespace(get=mock.Mock(return_value=response_context))
        session_context = mock.MagicMock()
        session_context.__aenter__.return_value = session
        task = {"code": 10000, "data": {"status": "done", "video_url": "https://example.com/v"}}
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(media.folder_paths, "get_temp_directory", return_value=directory),
            mock.patch.object(media.aiohttp, "TCPConnector"),
            mock.patch.object(media.aiohttp, "ClientSession", return_value=session_context),
        ):
            video, complete = await media.collect_video(task, "test-task")
            self.assertTrue(complete)
            self.assertEqual(video.get_dimensions(), (16, 16))
        session.get.assert_called_once()
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])

    async def test_five_minute_stereo_audio_fits_upload_limit_without_truncation(self):
        for sample_rate in (44100, 48000):
            with self.subTest(sample_rate=sample_rate):
                audio = {"waveform": torch.full((1, 2, sample_rate * 300), 0.125), "sample_rate": sample_rate}
                encoded = media.encode_audio(audio)
                self.assertLess(len(encoded), 15_000_000)
                with wave.open(BytesIO(encoded), "rb") as clip:
                    self.assertEqual(clip.getnchannels(), 1)
                    self.assertEqual(clip.getsampwidth(), 2)
                    self.assertEqual(clip.getframerate(), 24000)
                    self.assertEqual(clip.getnframes(), 24000 * 300)

    async def test_audio_that_fits_is_not_downmixed_or_resampled(self):
        for channels, sample_rate, seconds in ((2, 48000, 4), (1, 24000, 300)):
            with self.subTest(channels=channels, sample_rate=sample_rate, seconds=seconds):
                audio = {
                    "waveform": torch.zeros(1, channels, sample_rate * seconds),
                    "sample_rate": sample_rate,
                }
                with wave.open(BytesIO(media.encode_audio(audio)), "rb") as clip:
                    self.assertEqual(clip.getnchannels(), channels)
                    self.assertEqual(clip.getframerate(), sample_rate)
                    self.assertEqual(clip.getnframes(), sample_rate * seconds)

    async def test_audio_between_decimal_and_binary_limits_is_reduced_without_truncation(self):
        audio = {"waveform": torch.zeros(1, 2, 16000 * 240), "sample_rate": 16000}
        encoded = media.encode_audio(audio)
        self.assertLess(len(encoded), 15_000_000)
        with wave.open(BytesIO(encoded), "rb") as clip:
            self.assertEqual(clip.getnchannels(), 1)
            self.assertEqual(clip.getnframes(), 16000 * 240)

    async def test_audio_one_sample_over_five_minutes_is_rejected_before_upload(self):
        audio = {"waveform": torch.zeros(1, 1, 16000 * 300 + 1), "sample_rate": 16000}
        with mock.patch.object(media, "upload_bytes_to_comfy_storage") as upload:
            with self.assertRaises(api.BytePlusException):
                await media.prepare_media(None, audio, "https://example.com/i", "")
        upload.assert_not_called()

    async def test_encoded_size_limit_rejects_equal_boundary(self):
        image = torch.zeros(1, 300, 300, 3)
        audio = {"waveform": torch.zeros(1, 1, 64000), "sample_rate": 16000}
        for encoder, value, constant in (
            (media.encode_image, image, "IMAGE_MAX_BYTES"),
            (media.encode_audio, audio, "AUDIO_MAX_BYTES"),
        ):
            size = len(encoder(value))
            with mock.patch.object(media, constant, size), self.assertRaises(api.BytePlusException):
                encoder(value)
            with mock.patch.object(media, constant, size + 1):
                self.assertEqual(len(encoder(value)), size)

    async def test_connected_media_is_encoded_and_validated_before_upload(self):
        image = torch.zeros(1, 300, 300, 3)
        audio = {"waveform": torch.zeros(1, 1, 4 * 16000), "sample_rate": 16000}
        prepared = await media.prepare_media(image, audio, "", "")
        self.assertTrue(prepared.image_bytes.startswith(b"\x89PNG"))
        self.assertTrue(prepared.audio_bytes.startswith(b"RIFF"))
        with mock.patch.object(
            media, "upload_bytes_to_comfy_storage", side_effect=["https://example.com/i", "https://example.com/a"]
        ) as upload:
            urls = await media.upload_media(object, prepared)
        self.assertEqual(urls, ("https://example.com/i", "https://example.com/a"))
        self.assertEqual(upload.await_count, 2)

    async def test_invalid_batches_dimensions_ratio_audio_duration_and_nonfinite_data_fail(self):
        image = torch.zeros(1, 300, 300, 3)
        audio = {"waveform": torch.zeros(1, 1, 64000), "sample_rate": 16000}
        for bad in (
            torch.zeros(2, 300, 300, 3),
            torch.zeros(1, 299, 300, 3),
            torch.zeros(1, 300, 1000, 3),
            torch.full_like(image, float("nan")),
        ):
            with self.subTest(shape=bad.shape), self.assertRaises(api.BytePlusException):
                await media.prepare_media(bad, audio, "", "")
        for bad in (
            {},
            {**audio, "sample_rate": 0},
            {**audio, "waveform": torch.zeros(1, 1, 63999)},
            {**audio, "waveform": torch.zeros(2, 1, 64000)},
            {**audio, "waveform": torch.full_like(audio["waveform"], float("inf"))},
        ):
            with self.assertRaises(api.BytePlusException):
                await media.prepare_media(image, bad, "", "")

    async def test_sources_are_exclusive_and_url_mode_needs_no_upload(self):
        prepared = await media.prepare_media(None, None, "https://example.com/i", "https://example.com/a")
        with mock.patch.object(media, "upload_bytes_to_comfy_storage") as upload:
            self.assertEqual(
                await media.upload_media(object, prepared), ("https://example.com/i", "https://example.com/a")
            )
        upload.assert_not_called()
        with self.assertRaises(api.BytePlusException):
            await media.prepare_media(
                torch.zeros(1, 300, 300, 3), None, "https://example.com/i", "https://example.com/a"
            )

    async def test_download_failure_blocks_video_and_keeps_recovery_id_without_signed_url(self):
        task = {"code": 10000, "data": {"status": "done", "video_url": "https://example.com/v?Signature=private"}}
        with mock.patch.object(media, "download_to_file", side_effect=OSError("private")):
            video, complete = await media.collect_video(task, "test-task")
        self.assertFalse(complete)
        self.assertIsInstance(video, ExecutionBlocker)
        self.assertIn("test-task", str(video.message))
        self.assertNotIn("private", str(video.message))


@requires_comfyui
class NodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        patch = mock.patch.object(nodes, "handle_directory", return_value=self.folder.name)
        patch.start()
        self.addCleanup(patch.stop)
        for node in nodes.NODES:
            patch = mock.patch.object(node, "hidden", SimpleNamespace(unique_id="test-node"))
            patch.start()
            self.addCleanup(patch.stop)
        self.client = api.OmniHumanClient("fake-vision-key")
        self.arguments = {"image_url": "https://example.com/i", "audio_url": "https://example.com/a"}

    def test_schemas_register_without_key_sockets_and_have_stable_outputs(self):
        self.assertEqual(len(nodes.NODES), 3)
        for node in nodes.NODES:
            info = node.GET_NODE_INFO_V1()
            self.assertEqual(info["category"], "BytePlus ModelArk/OmniHuman")
            self.assertFalse(info["output_node"])
            inputs = {**info["input"]["required"], **info["input"].get("optional", {})}
            self.assertFalse(set(inputs) & {"api_key", "client", "omnihuman_client"})
        info = nodes.BytePlusOmniHuman.GET_NODE_INFO_V1()
        self.assertEqual(info["output_name"], ["video", "task_id", "response"])
        self.assertEqual(info["input"]["required"]["seed"][1]["control_after_generate"], "fixed")
        self.assertNotEqual(
            nodes.BytePlusOmniHumanTaskResult.fingerprint_inputs(),
            nodes.BytePlusOmniHumanTaskResult.fingerprint_inputs(),
        )

    async def test_missing_key_and_invalid_media_never_submit(self):
        with mock.patch.object(nodes, "submit_task") as submit:
            with self.assertRaises(api.BytePlusException):
                await nodes.BytePlusOmniHuman.execute(**self.arguments)
            with self.assertRaises(api.BytePlusException):
                await nodes.BytePlusOmniHuman.execute(self.client, **{**self.arguments, "image_url": ""})
        submit.assert_not_called()

    async def test_generation_returns_video_and_clears_completed_handle(self):
        task = {"code": 10000, "data": {"status": "done", "video_url": "https://example.com/v"}}
        with (
            mock.patch.object(nodes, "submit_task", return_value="test-task") as submit,
            mock.patch.object(nodes, "wait_for_task", return_value=task),
            mock.patch.object(nodes, "collect_video", return_value=("video", True)),
        ):
            result = await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
        self.assertEqual(result.result[:2], ("video", "test-task"))
        self.assertEqual(submit.await_count, 1)
        self.assertEqual(os.listdir(self.folder.name), [])

    async def test_interrupt_saves_recovery_and_requeue_does_not_submit_again(self):
        with (
            mock.patch.object(nodes, "submit_task", return_value="test-task") as submit,
            mock.patch.object(
                nodes, "wait_for_task", side_effect=comfy.model_management.InterruptProcessingException()
            ),
        ):
            with self.assertRaises(comfy.model_management.InterruptProcessingException):
                await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
        self.assertEqual(submit.await_count, 1)
        with open(os.path.join(self.folder.name, os.listdir(self.folder.name)[0]), encoding="utf-8") as file:
            text = file.read()
        self.assertIn("test-task", text)
        self.assertNotIn("fake-vision-key", text)
        self.assertNotIn("https://", text)
        task = {"code": 10000, "data": {"status": "done", "video_url": "https://example.com/v"}}
        with (
            mock.patch.object(nodes, "submit_task") as submit,
            mock.patch.object(nodes, "wait_for_task", return_value=task),
            mock.patch.object(nodes, "collect_video", return_value=("video", True)),
        ):
            result = await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
        submit.assert_not_called()
        self.assertEqual(result.result[1], "test-task")

    async def test_changed_inputs_cannot_replace_unresolved_paid_task(self):
        with (
            mock.patch.object(nodes, "submit_task", return_value="test-task"),
            mock.patch.object(
                nodes, "wait_for_task", side_effect=comfy.model_management.InterruptProcessingException()
            ),
        ):
            with self.assertRaises(comfy.model_management.InterruptProcessingException):
                await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
        with mock.patch.object(nodes, "submit_task") as submit:
            with self.assertRaises(api.BytePlusException):
                await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments, prompt="different")
        submit.assert_not_called()

    async def test_unknown_submission_is_not_automatically_repeated(self):
        with mock.patch.object(nodes, "submit_task", side_effect=api.BytePlusException("acceptance unknown")) as submit:
            with self.assertRaises(api.BytePlusException):
                await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
            with self.assertRaises(api.BytePlusException):
                await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
        self.assertEqual(submit.await_count, 1)

    async def test_pending_result_never_submits_or_downloads(self):
        task = {"code": 10000, "data": {"status": "in_queue"}}
        with (
            mock.patch.object(nodes, "poll_task", return_value=task),
            mock.patch.object(nodes, "submit_task") as submit,
            mock.patch.object(nodes, "collect_video", return_value=(ExecutionBlocker(None), False)),
        ):
            result = await nodes.BytePlusOmniHumanTaskResult.execute(
                self.client, "test-task", wait_for_completion=False
            )
        submit.assert_not_called()
        self.assertIsInstance(result.result[0], ExecutionBlocker)
        self.assertEqual(result.result[1], "test-task")

    async def test_simultaneous_requeues_share_unresolved_submission(self):
        task = {"code": 10000, "data": {"status": "done", "video_url": "https://example.com/v"}}
        with (
            mock.patch.object(nodes, "submit_task", return_value="test-task") as submit,
            mock.patch.object(nodes, "wait_for_task", return_value=task),
            mock.patch.object(nodes, "collect_video", return_value=(ExecutionBlocker(None), False)),
        ):
            results = await asyncio.gather(
                nodes.BytePlusOmniHuman.execute(self.client, **self.arguments),
                nodes.BytePlusOmniHuman.execute(self.client, **self.arguments),
            )
        self.assertEqual(submit.await_count, 1)
        self.assertEqual([result.result[1] for result in results], ["test-task", "test-task"])

    async def test_corrupt_recovery_stops_before_upload_or_submission(self):
        with open(nodes._handle_path("test-node"), "w", encoding="utf-8") as file:
            file.write('{"task_id":"untrusted"}')
        with mock.patch.object(nodes, "upload_media") as upload, mock.patch.object(nodes, "submit_task") as submit:
            with self.assertRaises(api.BytePlusException):
                await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
        upload.assert_not_called()
        submit.assert_not_called()

    async def test_recovery_storage_failure_stops_before_paid_submission(self):
        with (
            mock.patch.object(nodes, "_write_handle", side_effect=api.BytePlusException("storage unavailable")),
            mock.patch.object(nodes, "submit_task") as submit,
        ):
            with self.assertRaises(api.BytePlusException):
                await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
        submit.assert_not_called()

    async def test_accepted_task_storage_failure_reports_id_and_keeps_protective_record(self):
        original = nodes._write_handle

        def write_handle(node_id, handle):
            if handle["task_id"] is not None:
                raise api.BytePlusException("storage unavailable")
            original(node_id, handle)

        with (
            mock.patch.object(nodes, "_write_handle", side_effect=write_handle),
            mock.patch.object(nodes, "submit_task", return_value="test-task") as submit,
        ):
            with self.assertRaises(api.BytePlusException) as caught:
                await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
        self.assertIn("test-task", str(caught.exception))
        self.assertEqual(submit.await_count, 1)
        self.assertIsNone(nodes._read_handle("test-node")["task_id"])
        self.assertEqual(os.stat(nodes._handle_path("test-node")).st_mode & 0o777, 0o600)

    async def test_definitive_rejection_clears_protective_record(self):
        error = api.RetryableRequestError("invalid image", retryable=False)
        error.definitive_rejection = True
        with mock.patch.object(nodes, "submit_task", side_effect=error):
            with self.assertRaises(api.BytePlusException):
                await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
        self.assertIsNone(nodes._read_handle("test-node"))

    async def test_changed_credentials_cannot_replace_unresolved_paid_task(self):
        with (
            mock.patch.object(nodes, "submit_task", return_value="test-task"),
            mock.patch.object(
                nodes, "wait_for_task", side_effect=comfy.model_management.InterruptProcessingException()
            ),
        ):
            with self.assertRaises(comfy.model_management.InterruptProcessingException):
                await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
        with mock.patch.object(nodes, "submit_task") as submit:
            with self.assertRaises(api.BytePlusException):
                await nodes.BytePlusOmniHuman.execute(api.OmniHumanClient("other-fake-key"), **self.arguments)
        submit.assert_not_called()

    async def test_explicit_recovery_preserves_original_inputs_for_subsequent_requeue(self):
        task = {"code": 10000, "data": {"status": "done", "video_url": "https://example.com/v"}}
        with (
            mock.patch.object(nodes, "submit_task", return_value="test-task") as submit,
            mock.patch.object(nodes, "wait_for_task", return_value=task),
            mock.patch.object(nodes, "collect_video", return_value=(ExecutionBlocker(None), False)),
        ):
            await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
            original = nodes._read_handle("test-node")
            await nodes.BytePlusOmniHuman.execute(self.client, existing_task_id="test-task")
            self.assertEqual(nodes._read_handle("test-node"), original)
            await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
        self.assertEqual(submit.await_count, 1)

    async def test_explicit_recovery_cannot_overwrite_a_different_unresolved_task_or_key(self):
        task = {"code": 10000, "data": {"status": "done", "video_url": "https://example.com/v"}}
        with (
            mock.patch.object(nodes, "submit_task", return_value="test-task"),
            mock.patch.object(nodes, "wait_for_task", return_value=task),
            mock.patch.object(nodes, "collect_video", return_value=(ExecutionBlocker(None), False)),
        ):
            await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
        original = nodes._read_handle("test-node")
        for client, task_id in ((self.client, "other-task"), (api.OmniHumanClient("other-key"), "test-task")):
            with (
                self.subTest(task_id=task_id),
                mock.patch.object(nodes, "wait_for_task") as poll,
                self.assertRaises(api.BytePlusException),
            ):
                await nodes.BytePlusOmniHuman.execute(client, existing_task_id=task_id)
            poll.assert_not_called()
            self.assertEqual(nodes._read_handle("test-node"), original)

    async def test_explicit_recovery_can_resolve_unknown_acceptance_without_losing_input_hash(self):
        with mock.patch.object(nodes, "submit_task", side_effect=api.BytePlusException("unknown")):
            with self.assertRaises(api.BytePlusException):
                await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
        original = nodes._read_handle("test-node")
        task = {"code": 10000, "data": {"status": "done", "video_url": "https://example.com/v"}}
        with (
            mock.patch.object(nodes, "submit_task") as submit,
            mock.patch.object(nodes, "wait_for_task", return_value=task),
            mock.patch.object(nodes, "collect_video", return_value=(ExecutionBlocker(None), False)),
        ):
            await nodes.BytePlusOmniHuman.execute(self.client, existing_task_id="accepted-task")
            self.assertEqual(nodes._read_handle("test-node"), {**original, "task_id": "accepted-task"})
            await nodes.BytePlusOmniHuman.execute(self.client, **self.arguments)
        submit.assert_not_called()
