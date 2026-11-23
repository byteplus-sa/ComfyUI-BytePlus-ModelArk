import asyncio
import base64
import importlib
import json
import os
import struct
import sys
import types
import unittest
from unittest import mock

COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
requires_comfyui = unittest.skipUnless(COMFY_ROOT, "Set COMFYUI_ROOT to run Seed TTS 3 tests.")

if COMFY_ROOT:
    sys.path.insert(0, COMFY_ROOT)
    importlib.import_module("utils")
    import comfy.cli_args

    comfy.cli_args.args.cpu = True
    PACKAGE_NAME = "byteplus_plugin_test"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [PLUGIN_ROOT]
        sys.modules[PACKAGE_NAME] = package
    speech = importlib.import_module(f"{PACKAGE_NAME}.nodes.speech_api")


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


def stream_response(*events, status=200):
    return speech.SpeechResponse(status, {}, "\n".join(json.dumps(event) for event in events).encode())


def complete_stream(pcm=None):
    pcm = pcm if pcm is not None else struct.pack("<4h", 0, 16384, -16384, 32767)
    return stream_response(
        {"event": "ready", "session_id": "test-session"},
        {"event": "audio", "audio": base64.b64encode(pcm[:3]).decode()},
        {"event": "alignment", "text": "Hello", "words": [{"word": "Hello", "startTime": 0.1, "endTime": 0.4}]},
        {"event": "audio", "audio": base64.b64encode(pcm[3:]).decode()},
        {"event": "final", "is_final": True, "reason": "TTS_STREAM_FINAL_REASON_CLOSED"},
    )


@requires_comfyui
class SeedTTS3Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.nodes = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_seed_tts3")
        self.config = importlib.import_module(f"{PACKAGE_NAME}.nodes.seed_tts3_config")
        self.client = speech.SeedSpeechClient("tts3-test-secret")

    def build(self, **kwargs):
        return self.nodes.build_tts3_request(**{"text": "Hello", "voice": self.config.DEFAULT_TTS_3_VOICE, **kwargs})

    async def run_node(self, **kwargs):
        return await self.nodes.BytePlusSeedTTS3.execute(
            self.client, **{"text": "Hello", "voice": self.config.DEFAULT_TTS_3_VOICE, **kwargs}
        )

    def test_catalog_and_schema(self):
        schema = self.nodes.BytePlusSeedTTS3.define_schema()
        inputs = {item.id: item for item in schema.inputs}
        self.assertEqual(schema.node_id, "BytePlusSeedTTS3")
        self.assertFalse(schema.is_output_node)
        self.assertEqual(inputs["voice"].options, self.config.TTS_3_VOICE_IDS)
        self.assertIn(inputs["voice"].default, inputs["voice"].options)
        self.assertNotIn("speech_client", inputs)
        self.assertNotIn("api_key", inputs)
        self.assertGreater(len(self.config.TTS_3_VOICES), 400)
        self.assertEqual(len(self.config.TTS_3_VOICE_IDS), len(set(self.config.TTS_3_VOICE_IDS)))
        self.assertTrue(all(voice.isascii() and "\\" not in voice for voice in self.config.TTS_3_VOICE_IDS))
        self.assertIn("multi_female_annika_v3_uranus_bigtts", self.config.TTS_3_VOICE_IDS)
        self.assertIn("fil", self.config.TTS_3_SUBTITLE_LANGUAGES)
        self.assertIn("zh-CN-x-sichuan", self.config.TTS_3_LANGUAGES)
        self.assertNotIn("hi", self.config.TTS_3_SUBTITLE_LANGUAGES)

    def test_default_request_uses_native_body_and_pcm(self):
        path, body = self.build()
        self.assertEqual(path, f"/api/v3/text-to-speech/{self.config.DEFAULT_TTS_3_VOICE}/streaming")
        self.assertEqual(
            body,
            {
                "text": "Hello",
                "model_id": "dola-seed-tts-3.0",
                "language_code": "auto",
                "output_format": {"format": "pcm", "sample_rate": 24000},
                "voice_settings": {"speed": 1.0, "volume": 1.0, "pitch": 0},
                "enable_subtitle": False,
                "text_normalization": True,
                "enable_markdown_filter": False,
                "silence_duration": 0,
            },
        )

    def test_optional_controls_and_voice_path_encoding(self):
        path, body = self.build(
            custom_speaker_id="custom/voice?#",
            instructions=" Warm and natural \n\n Speak slowly ",
            language_code="fil",
            speed=0.5,
            volume=2.0,
            pitch=-12,
            sample_rate="48000",
            enable_subtitle=True,
            similarity_boost=True,
            session_id="my-conversation",
            text_normalization=False,
            enable_markdown_filter=True,
            silence_duration=30000,
        )
        self.assertEqual(path, "/api/v3/text-to-speech/custom%2Fvoice%3F%23/streaming")
        self.assertEqual(
            body["voice_settings"],
            {
                "speed": 0.5,
                "volume": 2.0,
                "pitch": -12,
                "instructions": ["Warm and natural", "Speak slowly"],
                "similarity_boost": True,
            },
        )
        self.assertEqual(body["session_id"], "my-conversation")
        self.assertEqual(body["language_code"], "fil")
        self.assertTrue(body["enable_subtitle"])
        self.assertFalse(body["text_normalization"])
        self.assertEqual(body["output_format"], {"format": "pcm", "sample_rate": 48000})

    def test_ssml_and_text_boundary(self):
        _, body = self.build(text="", ssml="<speak>Hello</speak>")
        self.assertEqual(body["ssml"], "<speak>Hello</speak>")
        self.assertNotIn("text", body)
        self.assertEqual(len(self.build(text="a" * 10000)[1]["text"]), 10000)
        for sample_rate in ("8000", "16000", "22050", "24000", "32000", "44100", "48000"):
            self.assertEqual(self.build(sample_rate=sample_rate)[1]["output_format"]["sample_rate"], int(sample_rate))

    async def test_invalid_inputs_fail_before_network(self):
        invalid = [
            {"text": ""},
            {"text": None},
            {"text": []},
            {"text": "a" * 10001},
            {"ssml": "<speak>Hello</speak>"},
            {"voice": ""},
            {"voice": "unknown"},
            {"custom_speaker_id": ".."},
            {"custom_speaker_id": "bad\nid"},
            {"speed": 0.49},
            {"speed": float("nan")},
            {"volume": 2.01},
            {"volume": True},
            {"pitch": 1.5},
            {"pitch": 13},
            {"sample_rate": "12000"},
            {"language_code": "xx"},
            {"language_code": "hi", "enable_subtitle": True},
            {"instructions": []},
            {"session_id": 123},
            {"silence_duration": -1},
            {"enable_subtitle": "true"},
            {"similarity_boost": 1},
        ]
        with mock.patch.object(speech, "_send", new_callable=mock.AsyncMock) as send:
            for kwargs in invalid:
                with self.subTest(kwargs=kwargs), self.assertRaises(self.nodes.BytePlusException):
                    await self.run_node(**kwargs)
            send.assert_not_called()

    async def test_execute_decodes_audio_subtitles_and_session(self):
        with mock.patch.object(speech, "_send", new_callable=mock.AsyncMock, return_value=complete_stream()) as send:
            audio, subtitles, srt, session_id = (await self.run_node(sample_rate="48000", enable_subtitle=True)).result
        args = send.call_args.args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], self.client.base_url + self.build()[0])
        self.assertEqual(args[2]["X-Api-Key"], self.client.api_key)
        self.assertIn("X-Request-Id", args[2])
        self.assertNotIn("X-Api-Request-Id", args[2])
        self.assertNotIn("X-Api-Resource-Id", args[2])
        self.assertNotIn("X-Api-App-Key", args[2])
        self.assertEqual(audio["sample_rate"], 48000)
        self.assertEqual(tuple(audio["waveform"].shape), (1, 1, 4))
        self.assertAlmostEqual(float(audio["waveform"][0, 0, 1]), 0.5)
        self.assertIn("00:00:00,100 --> 00:00:00,400\nHello", srt)
        self.assertEqual(json.loads(subtitles)["segments"][0]["start_ms"], 100)
        self.assertEqual(session_id, "test-session")
        send.assert_awaited_once()

    async def test_empty_malformed_or_incomplete_stream_is_not_returned(self):
        ready = {"event": "ready", "session_id": "test"}
        audio = {"event": "audio", "audio": "AAA="}
        final = {"event": "final", "is_final": True, "reason": "TTS_STREAM_FINAL_REASON_CLOSED"}
        responses = [
            stream_response(),
            stream_response(ready, audio),
            stream_response(ready, final),
            stream_response(audio, final),
            stream_response(ready, {"event": "audio", "audio": "!!!"}, final),
            stream_response(ready, {"event": "audio", "audio": "AA=="}, final),
            stream_response(ready, audio, {**final, "reason": "interrupted"}),
            stream_response(ready, audio, {**final, "is_final": False}),
            stream_response(ready, {"event": "alignment", "words": [{"startTime": -1, "endTime": 0}]}, audio, final),
            speech.SpeechResponse(200, {}, b'{"event":"ready"}\n{"event":'),
            speech.SpeechResponse(200, {}, b"not json"),
        ]
        for response in responses:
            with (
                self.subTest(body=response.body),
                mock.patch.object(speech, "_send", new_callable=mock.AsyncMock, return_value=response),
                self.assertRaises(self.nodes.BytePlusException),
            ):
                await self.run_node()

    async def test_http_and_stream_errors_preserve_codes_and_redact_key(self):
        event = {
            "event": "error",
            "error": {
                "type": "authorization_error",
                "code": "voice_access_denied",
                "message": f"Denied {self.client.api_key}",
                "param": "voice_id",
                "request_id": "request-test",
                "logid": "log-test",
            },
        }
        for status in (200, 403, 429, 503):
            with (
                self.subTest(status=status),
                mock.patch.object(
                    speech, "_send", new_callable=mock.AsyncMock, return_value=stream_response(event, status=status)
                ) as send,
                self.assertRaises(self.nodes.BytePlusException) as caught,
            ):
                await self.run_node()
            message = str(caught.exception)
            self.assertIn("voice_access_denied", message)
            self.assertIn("log-test", message)
            self.assertIn("request-test", message)
            self.assertNotIn(self.client.api_key, message)
            send.assert_awaited_once()

    async def test_concurrent_calls_do_not_share_audio_or_request_ids(self):
        with mock.patch.object(
            speech,
            "_send",
            new_callable=mock.AsyncMock,
            side_effect=[complete_stream(struct.pack("<2h", 1, 2)), complete_stream(struct.pack("<4h", 3, 4, 5, 6))],
        ) as send:
            results = await asyncio.gather(self.run_node(), self.run_node())
        self.assertEqual(sorted(result.result[0]["waveform"].shape[-1] for result in results), [2, 4])
        self.assertEqual(len({call.args[2]["X-Request-Id"] for call in send.call_args_list}), 2)

    async def test_interrupt_cancels_the_single_request(self):
        import comfy.model_management as mm

        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def pending(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with mock.patch.object(speech, "_send", side_effect=pending) as send:
            task = asyncio.create_task(self.run_node())
            await started.wait()
            mm.interrupt_current_processing(True)
            try:
                with self.assertRaises(mm.InterruptProcessingException):
                    await task
            finally:
                mm.interrupt_current_processing(False)
        self.assertTrue(cancelled.is_set())
        send.assert_called_once()
