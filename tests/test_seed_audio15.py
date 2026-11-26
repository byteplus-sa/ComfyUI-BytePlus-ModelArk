import asyncio
import base64
import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_NAME = "byteplus_plugin_test"
requires_comfyui = unittest.skipUnless(COMFY_ROOT, "Set COMFYUI_ROOT to run Seed Audio 1.5 tests.")

if COMFY_ROOT:
    sys.path.insert(0, COMFY_ROOT)
    importlib.import_module("utils")
    import comfy.cli_args

    comfy.cli_args.args.cpu = True
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [PLUGIN_ROOT]
        sys.modules[PACKAGE_NAME] = package


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


@requires_comfyui
class SeedAudio15Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = importlib.import_module(f"{PACKAGE_NAME}.nodes.seed_audio15_api")
        self.media = importlib.import_module(f"{PACKAGE_NAME}.nodes.seed_audio15_media")
        self.nodes = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_seed_audio15")
        self.models = importlib.import_module(f"{PACKAGE_NAME}.nodes.models_config")
        self.shared = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_shared")
        self.torch = importlib.import_module("torch")
        self.Blocker = importlib.import_module("comfy_execution.graph_utils").ExecutionBlocker
        self.client = types.SimpleNamespace(api_key="modelark-test-secret", region="ap-southeast-1")
        self.hidden = mock.patch.object(
            self.nodes.BytePlusSeedAudio15, "hidden", types.SimpleNamespace(unique_id="test-audio15")
        )
        self.hidden.start()
        self.addCleanup(self.hidden.stop)
        self.default_mode = {"mode": "text to audio", "text_prompt": "A warm English voice says: Hello."}

    def audio(self, seconds=2, channels=1, batch=1):
        return {"waveform": self.torch.zeros(batch, channels, int(seconds * 8000)), "sample_rate": 8000}

    def response(self, **extra):
        return {
            "id": "generation-test",
            "content": {"audios": [{"url": "https://media.example.com/one.wav", "type": "speaker"}]},
            "output_format": "wav",
            "usage": {"duration_ms": 2000, "final_duration_ms": 2000},
            **extra,
        }

    def wire(self, response=None, status=200):
        return status, {"X-Request-Id": "request-test"}, json.dumps(response or self.response()).encode()

    def build(self, mode=None, **kwargs):
        mode = mode or self.default_mode
        content, _ = self.media.prepare_content(mode)
        return self.api.build_request(mode["mode"], content, **kwargs)

    async def run_node(self, mode=None, **kwargs):
        return await self.nodes.BytePlusSeedAudio15.execute(self.client, mode or self.default_mode, **kwargs)

    def test_catalog_schema_and_old_audio_are_independent(self):
        self.assertEqual(self.models.SEED_AUDIO_15_MODEL_MAP, {"dola-seed-audio-1-5": "dola-seed-audio-1-5-261128"})
        self.assertEqual(self.models.SEED_AUDIO_MODELS, ["seed-audio-1.0"])
        schema = self.nodes.BytePlusSeedAudio15.define_schema()
        self.assertFalse(schema.is_output_node)
        self.assertEqual(schema.category, "BytePlus ModelArk/Audio")
        inputs = {item.id: item for item in schema.inputs}
        self.assertEqual([option.key for option in inputs["mode"].options], list(self.api.MODES))
        self.assertEqual(inputs["sample_rate"].default, "44100")
        self.assertNotIn("pcm", inputs["output_format"].options)
        self.assertNotIn("client", inputs)
        self.assertIn("Not sent to Seed Audio 1.5", inputs["seed"].tooltip)
        self.assertEqual(
            [item.is_output_list for item in schema.outputs],
            [True, False, False, False, False, False, True, True, True, True, False, False],
        )
        translation = inputs["mode"].options[4]
        self.assertNotIn("text_prompt", {item.id for item in translation.inputs})

    def test_text_request_matches_native_contract(self):
        self.assertEqual(
            self.build(),
            {
                "model": "dola-seed-audio-1-5-261128",
                "content": [{"type": "text", "text": self.default_mode["text_prompt"]}],
                "output_format": "wav",
                "audio_config": {"sample_rate": 44100, "speech_rate": 0, "loudness_rate": 0, "pitch_rate": 0},
            },
        )
        for field in ("seed", "text_prompt", "references", "watermark", "dubbing_config"):
            self.assertNotIn(field, self.build())

    def test_six_reference_slots_are_ordered_and_inline(self):
        mode = {"mode": "audio reference", "text_prompt": "Use @audio1 and @audio6."}
        for slot in range(1, 7):
            mode[f"ref_audio_{slot}_url"] = f"https://media.example.com/{slot}.wav"
        mode.pop("ref_audio_1_url")
        mode["reference_audio_1"] = self.audio(channels=2)
        body = self.build(mode)
        refs = body["content"][1:]
        self.assertEqual(len(refs), 6)
        self.assertTrue(refs[0]["audio_url"]["url"].startswith("data:audio/wav;base64,"))
        self.assertEqual(refs[5]["audio_url"]["url"], "https://media.example.com/6.wav")
        self.assertEqual({item["role"] for item in refs}, {"reference_audio"})

    def test_video_guided_request_with_voice_and_includes(self):
        body = self.build(
            {
                "mode": "video to audio",
                "text_prompt": "Read with @audio1.",
                "video_url": "https://media.example.com/video.mp4",
                "ref_audio_1_url": "https://media.example.com/voice.wav",
            },
            include_video=True,
            include_subtitles=True,
        )
        self.assertEqual([item.get("role") for item in body["content"]], [None, "reference_audio", "reference_video"])
        self.assertEqual(body["include"], ["video_url", "subtitles.srt", "subtitles.jsonl"])
        self.assertNotIn("dubbing_config", body)

    def test_translation_and_custom_subtitle_contract(self):
        content, _ = self.media.prepare_content(
            {"mode": "video translation", "video_url": "tos://my-bucket/source.mp4"}
        )
        body = self.api.build_request(
            "video translation",
            content,
            target_language="fr",
            source_language="en",
            custom_subtitle_url="https://media.example.com/target.srt",
            dialogue_enhancement="medium",
            include_video=True,
            include_subtitles=True,
            include_source_subtitles=True,
        )
        self.assertEqual(
            body["content"],
            [{"type": "video_url", "video_url": {"url": "tos://my-bucket/source.mp4"}, "role": "dubbing_video"}],
        )
        self.assertEqual(body["audio_config"], {"sample_rate": 44100})
        self.assertEqual(
            body["dubbing_config"],
            {
                "target_language": "fr",
                "source_language": "en",
                "dialogue_enhancement": "medium",
                "custom_subtitle": {"format": "srt", "url": "https://media.example.com/target.srt"},
            },
        )
        self.assertEqual(len(body["include"]), 5)
        for language in self.api.TARGET_LANGUAGES:
            with self.subTest(language=language):
                self.assertEqual(
                    self.api.build_request("video translation", content, target_language=language)["dubbing_config"],
                    {"target_language": language},
                )
        self.assertEqual(
            self.api.build_request(
                "video translation",
                content,
                target_language="en",
                glossaries='[{"source":"brand","target":"BytePlus"}]',
            )["dubbing_config"]["glossaries"][0]["target"],
            "BytePlus",
        )

    def test_separation_request_has_one_source_and_no_generation_controls(self):
        mode = {
            "mode": "stem separation",
            "text_prompt": "Separate vocals, music and effects.",
            "audio_url": "https://media.example.com/source.mp3",
        }
        body = self.build(mode)
        self.assertEqual(body["content"][1]["role"], "separate_audio")
        self.assertEqual(body["audio_config"], {"sample_rate": 44100})
        self.assertNotIn("include", body)
        mode["audio"] = self.audio(seconds=31)
        mode.pop("audio_url")
        self.assertTrue(self.build(mode)["content"][1]["audio_url"]["url"].startswith("data:audio/wav;base64,"))

    def test_audio_image_and_video_boundaries(self):
        for seconds in (2, 30):
            self.media.audio_data(self.audio(seconds=seconds))
        for seconds in (1.9, 30.1):
            with self.assertRaises(self.shared.BytePlusException):
                self.media.audio_data(self.audio(seconds=seconds))
        self.media.audio_data(self.audio(seconds=360), separation=True)
        with self.assertRaises(self.shared.BytePlusException):
            self.media.audio_data(self.audio(seconds=360.1), separation=True)
        for audio in (self.audio(batch=2), self.audio(channels=3)):
            with self.assertRaises(self.shared.BytePlusException):
                self.media.audio_data(audio)
        valid_image = self.torch.zeros(1, 300, 300, 3)
        self.assertTrue(self.media.image_data(valid_image).startswith("data:image/png;base64,"))
        for image in (
            self.torch.zeros(2, 300, 300, 3),
            self.torch.zeros(1, 299, 300, 3),
            self.torch.zeros(1, 300, 800, 3),
        ):
            with self.assertRaises(self.shared.BytePlusException):
                self.media.image_data(image)
        video = types.SimpleNamespace(
            get_duration=lambda: 4,
            get_frame_rate=lambda: 24,
            get_dimensions=lambda: (1280, 720),
            get_stream_source=lambda: None,
        )
        self.media.validate_video(video)
        video.get_duration = lambda: 361
        with self.assertRaises(self.shared.BytePlusException):
            self.media.validate_video(video)

    async def test_invalid_inputs_fail_before_upload_or_generation(self):
        modes = [
            {"mode": "unknown"},
            {"mode": "text to audio", "text_prompt": ""},
            {"mode": "text to audio", "text_prompt": "@audio1 says hello."},
            {"mode": "audio reference", "text_prompt": "Hello"},
            {
                "mode": "audio reference",
                "text_prompt": "@audio2",
                "ref_audio_2_url": "https://media.example.com/two.wav",
            },
            {
                "mode": "audio reference",
                "text_prompt": "Hi",
                "reference_audio_1": self.audio(),
                "ref_audio_1_url": "https://media.example.com/one.wav",
            },
            {"mode": "video translation", "video_url": "https://media.example.com/a.mp4", "text_prompt": "Forbidden"},
            {
                "mode": "video translation",
                "video_url": "https://media.example.com/a.mp4",
                "custom_subtitle_url": "https://media.example.com/a.srt",
                "glossaries": '[{"source":"a","target":"b"}]',
            },
            {"mode": "image reference", "text_prompt": "Hello"},
            {"mode": "stem separation", "text_prompt": "Separate", "audio_url": "http://127.0.0.1/a.wav"},
            {"mode": "video to audio", "text_prompt": "Hello", "video_url": "https://user:secret@example.com/a.mp4"},
            {"mode": "video translation", "video_url": "tos://bucket/a.mp4?token=x"},
        ]
        with (
            mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock) as send,
            mock.patch.object(self.nodes, "upload_video", new_callable=mock.AsyncMock) as upload,
        ):
            for mode in modes:
                with self.subTest(mode=mode), self.assertRaises(self.shared.BytePlusException):
                    await self.run_node(mode)
            for kwargs in (
                {"sample_rate": "12000"},
                {"output_format": "pcm"},
                {"output_format": "ogg_opus"},
                {"speech_rate": 101},
                {"pitch_rate": True},
                {"loudness_rate": -51},
                {"include_video": True},
                {"include_source_subtitles": True},
                {"include_subtitles": "true"},
            ):
                with self.subTest(kwargs=kwargs), self.assertRaises(self.shared.BytePlusException):
                    await self.run_node(**kwargs)
        send.assert_not_called()
        upload.assert_not_called()

    def test_invalid_translation_controls_and_request_size(self):
        content, _ = self.media.prepare_content(
            {"mode": "video translation", "video_url": "https://media.example.com/a.mp4"}
        )
        for kwargs in (
            {"target_language": "xx"},
            {"source_language": "fr"},
            {"dialogue_enhancement": "max"},
            {"glossaries": "not json"},
            {"glossaries": "{}"},
            {"glossaries": '[{"source":"","target":"b"}]'},
            {"speech_rate": 1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(self.shared.BytePlusException):
                self.api.build_request("video translation", content, **{"target_language": "en", **kwargs})
        with (
            mock.patch.object(self.api, "MAX_REQUEST_BYTES", 10),
            self.assertRaisesRegex(self.shared.BytePlusException, "64 MB"),
        ):
            self.build()

    async def test_omitted_optional_inputs_use_defaults_without_changing_request(self):
        optional = {item.id: None for item in self.nodes.BytePlusSeedAudio15.define_schema().inputs if item.optional}
        with (
            mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock, return_value=self.wire()) as send,
            mock.patch.object(self.media, "download_artifact", new_callable=mock.AsyncMock, return_value=self.audio()),
        ):
            await self.run_node(**optional)
        self.assertEqual(send.call_args.args[2], self.build())

        mode = {
            "mode": "video translation",
            "video_url": "https://media.example.com/a.mp4",
            "target_language": "fr",
            "source_language": None,
            "dialogue_enhancement": None,
            "custom_subtitle_url": None,
            "glossaries": None,
        }
        with (
            mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock, return_value=self.wire()) as send,
            mock.patch.object(self.media, "download_artifact", new_callable=mock.AsyncMock, return_value=self.audio()),
        ):
            await self.run_node(mode, **optional)
        self.assertEqual(send.call_args.args[2]["dubbing_config"], {"target_language": "fr"})

    async def test_invalid_translation_values_never_silently_become_defaults(self):
        mode = {
            "mode": "video translation",
            "video_url": "https://media.example.com/a.mp4",
            "target_language": "fr",
        }
        overrides = [{"target_language": value} for value in (None, "", False, 0, [], {})] + [
            {field: value}
            for field in ("source_language", "dialogue_enhancement", "glossaries")
            for value in (False, 0, [], {})
        ]
        overrides.extend([{"source_language": ""}, {"dialogue_enhancement": ""}])
        with (
            mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock) as send,
            mock.patch.object(self.nodes, "upload_video", new_callable=mock.AsyncMock) as upload,
        ):
            for override in overrides:
                with self.subTest(override=override), self.assertRaises(self.shared.BytePlusException):
                    await self.run_node({**mode, **override})
            missing_target = {key: value for key, value in mode.items() if key != "target_language"}
            with self.assertRaises(self.shared.BytePlusException):
                await self.run_node(missing_target)
        send.assert_not_called()
        upload.assert_not_called()

    def test_decimal_byte_limits_and_serialized_request_boundary(self):
        self.assertEqual(self.api.MAX_REQUEST_BYTES, 64_000_000)
        self.assertEqual(self.media.MAX_REFERENCE_AUDIO_BYTES, 15_000_000)
        self.assertEqual(self.media.MAX_SEPARATION_AUDIO_BYTES, 90_000_000)
        self.assertEqual(self.media.MAX_IMAGE_BYTES, 10_000_000)
        self.assertEqual(self.media.MAX_PUBLIC_VIDEO_BYTES, 200_000_000)
        body = {"text": "\u00e9", "audio": "data:audio/wav;base64,AAAA"}
        wire_size = len(json.dumps(body).encode("utf-8"))
        self.assertGreater(wire_size, len(json.dumps(body, ensure_ascii=False).encode("utf-8")))
        with mock.patch.object(self.api, "MAX_REQUEST_BYTES", wire_size):
            self.api.request_size(body)
        with (
            mock.patch.object(self.api, "MAX_REQUEST_BYTES", wire_size - 1),
            self.assertRaisesRegex(self.shared.BytePlusException, "64 MB"),
        ):
            self.api.request_size(body)

    def test_encoded_audio_and_image_size_boundaries(self):
        audio = self.audio()
        encoded = self.media.audio_to_wav_bytes(audio)
        for limit_name in ("MAX_REFERENCE_AUDIO_BYTES", "MAX_SEPARATION_AUDIO_BYTES"):
            separation = limit_name == "MAX_SEPARATION_AUDIO_BYTES"
            with mock.patch.object(self.media, limit_name, len(encoded)):
                self.media.audio_data(audio, separation=separation)
            with (
                mock.patch.object(self.media, limit_name, len(encoded) - 1),
                mock.patch.object(self.media, "audio_to_wav_bytes") as encode,
                self.assertRaises(self.shared.BytePlusException),
            ):
                self.media.audio_data(audio, separation=separation)
            encode.assert_not_called()
        image = self.torch.zeros(1, 300, 300, 3)
        encoded_size = len(base64.b64decode(self.media.image_data(image).split(",", 1)[1]))
        with mock.patch.object(self.media, "MAX_IMAGE_BYTES", encoded_size + 1):
            self.media.image_data(image)
        with (
            mock.patch.object(self.media, "MAX_IMAGE_BYTES", encoded_size),
            self.assertRaisesRegex(self.shared.BytePlusException, "smaller than 10 MB"),
        ):
            self.media.image_data(image)

    def test_connected_video_decimal_size_boundary(self):
        video = types.SimpleNamespace(
            get_duration=lambda: 4,
            get_frame_rate=lambda: 24,
            get_dimensions=lambda: (1280, 720),
        )
        with mock.patch.object(self.media, "video_source_size_bytes", return_value=200_000_000):
            self.media.validate_video(video)
        with (
            mock.patch.object(self.media, "video_source_size_bytes", return_value=200_000_001),
            self.assertRaisesRegex(self.shared.BytePlusException, "200 MB"),
        ):
            self.media.validate_video(video)

    async def test_unsupported_region_stops_before_submission(self):
        self.client.region = "eu-west-1"
        with (
            mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock) as send,
            mock.patch.object(self.nodes, "prepare_content") as prepare,
            self.assertRaisesRegex(
                self.shared.BytePlusException, "Availability in other regions has not been verified"
            ),
        ):
            await self.run_node()
        send.assert_not_called()
        prepare.assert_not_called()
        with (
            mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock) as send,
            self.assertRaises(self.shared.BytePlusException),
        ):
            await self.api.generate(self.client, self.build())
        send.assert_not_called()

    async def test_transport_uses_modelark_bearer_and_native_endpoint_once(self):
        with (
            mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock, return_value=self.wire()) as send,
            mock.patch.object(self.media, "download_artifact", new_callable=mock.AsyncMock, return_value=self.audio()),
        ):
            result = await self.run_node(seed=99)
        url, headers, body = send.call_args.args
        self.assertEqual(url, "https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations")
        self.assertEqual(headers["Authorization"], "Bearer modelark-test-secret")
        self.assertNotIn("X-Api-Key", headers)
        self.assertNotIn("seed", body)
        self.assertEqual(result.result[10], "generation-test")
        send.assert_awaited_once()

    async def test_connected_video_upload_occurs_after_validation(self):
        video = types.SimpleNamespace(
            get_duration=lambda: 4,
            get_frame_rate=lambda: 24,
            get_dimensions=lambda: (1280, 720),
            get_stream_source=lambda: None,
        )
        mode = {"mode": "video to audio", "text_prompt": "Hello", "video": video}
        with (
            mock.patch.object(
                self.nodes,
                "upload_video",
                new_callable=mock.AsyncMock,
                return_value="https://media.example.com/upload.mp4",
            ) as upload,
            mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock, return_value=self.wire()) as send,
            mock.patch.object(self.media, "download_artifact", new_callable=mock.AsyncMock, return_value=self.audio()),
        ):
            await self.run_node(mode)
        self.assertEqual(
            send.call_args.args[2]["content"][-1]["video_url"]["url"], "https://media.example.com/upload.mp4"
        )
        upload.assert_awaited_once()
        with (
            mock.patch.object(self.nodes, "upload_video", new_callable=mock.AsyncMock) as upload,
            self.assertRaises(self.shared.BytePlusException),
        ):
            await self.run_node(mode, pitch_rate=13)
        upload.assert_not_called()

    async def test_all_tracks_and_subtitle_files_retain_provider_order(self):
        content = {
            "audios": [
                {"url": "https://media.example.com/speaker.wav", "type": "speaker", "description": "Vocals"},
                {"url": "https://media.example.com/sfx.wav", "type": "sfx", "description": "Effects"},
                {"url": "https://media.example.com/bgm.wav", "type": "bgm", "description": "Music"},
            ],
            "video_url": "https://media.example.com/out.mp4",
            "subtitles": [
                {"format": "jsonl", "url": "https://media.example.com/t.jsonl"},
                {"format": "srt", "url": "https://media.example.com/t.srt"},
            ],
            "subtitles_source": [
                {"format": "srt", "url": "https://media.example.com/s.srt"},
                {"format": "jsonl", "url": "https://media.example.com/s.jsonl"},
            ],
        }

        async def download(url, kind):
            return self.audio() if kind == "audio" else url.rsplit("/", 1)[-1]

        includes = ["video_url", "subtitles.srt", "subtitles.jsonl", "subtitles_source.srt", "subtitles_source.jsonl"]
        with mock.patch.object(self.media, "download_artifact", side_effect=download):
            result = await self.media.collect_outputs(self.response(content=content), includes)
        self.assertEqual(len(result[0]), 3)
        self.assertEqual(result[1:6], ("out.mp4", "t.srt", "t.jsonl", "s.srt", "s.jsonl"))
        self.assertEqual(result[6], ["speaker", "sfx", "bgm"])
        self.assertEqual(result[7], ["Vocals", "Effects", "Music"])
        self.assertEqual(result[8], [2.0] * 3)
        self.assertEqual(result[9], [track["url"] for track in content["audios"]])

    async def test_encoded_video_limit_is_checked_even_when_source_size_is_small(self):
        video = types.SimpleNamespace(
            get_duration=lambda: 4,
            get_frame_rate=lambda: 24,
            get_dimensions=lambda: (1280, 720),
            get_stream_source=lambda: io.BytesIO(b"small source"),
            save_to=lambda buffer, **kwargs: buffer.write(b"x" * 101),
        )
        with (
            mock.patch.object(self.media, "MAX_PUBLIC_VIDEO_BYTES", 100),
            mock.patch.object(self.media, "upload_bytes_to_comfy_storage", new_callable=mock.AsyncMock) as upload,
            mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock) as send,
            self.assertRaisesRegex(self.shared.BytePlusException, "Encoded video exceeds 200 MB"),
        ):
            await self.run_node({"mode": "video to audio", "text_prompt": "Hello", "video": video})
        upload.assert_not_called()
        send.assert_not_called()

    async def test_real_trimmed_video_is_measured_before_upload_and_preserves_trim(self):
        av = importlib.import_module("av")
        numpy = importlib.import_module("numpy")
        buffer = io.BytesIO()
        with av.open(buffer, "w", format="mp4") as container:
            stream = container.add_stream("libx264", rate=12)
            stream.width, stream.height, stream.pix_fmt = 640, 640, "yuv420p"
            image = numpy.zeros((640, 640, 3), dtype=numpy.uint8)
            for _ in range(72):
                frame = av.VideoFrame.from_ndarray(image, format="rgb24")
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        video = self.media.VideoFromFile(buffer).as_trimmed(start_time=1, duration=4)
        self.assertIsNone(self.media.video_source_size_bytes(video))
        encoded = self.media.video_data(video)
        mode = {"mode": "video to audio", "text_prompt": "Hello", "video": video}
        with (
            mock.patch.object(self.media, "MAX_PUBLIC_VIDEO_BYTES", len(encoded) - 1),
            mock.patch.object(self.media, "upload_bytes_to_comfy_storage", new_callable=mock.AsyncMock) as upload,
            mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock) as send,
            self.assertRaisesRegex(self.shared.BytePlusException, "Encoded video exceeds 200 MB"),
        ):
            await self.run_node(mode)
        upload.assert_not_called()
        send.assert_not_called()
        with (
            mock.patch.object(self.media, "MAX_PUBLIC_VIDEO_BYTES", len(encoded)),
            mock.patch.object(
                self.media,
                "upload_bytes_to_comfy_storage",
                new_callable=mock.AsyncMock,
                return_value="https://media.example.com/trimmed.mp4",
            ) as upload,
        ):
            await self.media.upload_video(self.nodes.BytePlusSeedAudio15, video)
        uploaded = upload.call_args.args[1]
        self.assertEqual(uploaded, encoded)
        self.assertAlmostEqual(self.media.VideoFromFile(io.BytesIO(uploaded)).get_duration(), 4, places=2)

    async def test_connected_video_reuses_content_cached_uploads(self):
        video = types.SimpleNamespace(save_to=lambda buffer, **kwargs: buffer.write(b"unique audio15 video fixture"))
        helper = types.ModuleType("comfy_api_nodes.util")
        helper.upload_file_to_comfyapi = mock.AsyncMock(return_value="https://media.example.com/cached.mp4")
        with (
            mock.patch.dict(sys.modules, {"comfy_api_nodes.util": helper}),
            mock.patch.dict(self.shared.COMFY_UPLOAD_CACHE, {}, clear=True),
        ):
            first = await self.media.upload_video(self.nodes.BytePlusSeedAudio15, video)
            second = await self.media.upload_video(self.nodes.BytePlusSeedAudio15, video)
        self.assertEqual(first, second)
        helper.upload_file_to_comfyapi.assert_awaited_once()

    async def test_download_failures_block_only_affected_artifacts_and_preserve_urls(self):
        response = self.response()
        response["content"]["audios"].append({"url": "https://media.example.com/two.wav", "type": "bgm"})
        with mock.patch.object(
            self.media,
            "download_artifact",
            new_callable=mock.AsyncMock,
            side_effect=[RuntimeError("failed"), self.audio()],
        ):
            result = await self.media.collect_outputs(response, [])
        self.assertIsInstance(result[0][0], self.Blocker)
        self.assertIsInstance(result[1], self.Blocker)
        self.assertEqual(result[0][1]["sample_rate"], 8000)
        self.assertEqual(len(result[9]), 2)
        with mock.patch.object(self.media, "download_artifact", new_callable=mock.AsyncMock, return_value=self.audio()):
            result = await self.media.collect_outputs(self.response(), ["subtitles.srt"])
        self.assertIsInstance(result[2], self.Blocker)

    async def test_error_codes_request_id_and_keys_are_redacted_without_retry(self):
        for status in (400, 401, 429, 500, 200):
            error = {
                "error": {
                    "code": "InvalidParameter",
                    "message": f"Invalid {self.client.api_key} https://example.com/file?Signature=secret",
                }
            }
            with (
                self.subTest(status=status),
                mock.patch.object(
                    self.api, "_send", new_callable=mock.AsyncMock, return_value=self.wire(error, status)
                ) as send,
                self.assertRaises(self.shared.BytePlusException) as caught,
            ):
                await self.api.generate(self.client, self.build())
            self.assertNotIn(self.client.api_key, str(caught.exception))
            self.assertNotIn("Signature=secret", str(caught.exception))
            self.assertIn("request-test", str(caught.exception))
            self.assertIn("InvalidParameter", str(caught.exception))
            send.assert_awaited_once()

    async def test_malformed_or_empty_generation_response_is_rejected(self):
        responses = [
            b"not json",
            b"[]",
            b"{}",
            json.dumps({"id": "x", "content": {"audios": []}}).encode(),
        ]
        for data in responses:
            with (
                self.subTest(data=data),
                mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock, return_value=(200, {}, data)),
                self.assertRaises(self.shared.BytePlusException),
            ):
                await self.api.generate(self.client, self.build())

    async def test_malformed_track_preserves_good_tracks_and_recovery_response(self):
        response = self.response()
        response["content"]["audios"].extend(
            [{"type": "bgm", "description": "Missing URL"}, None, {"url": 12}, {"url": "  "}]
        )
        response["content"]["audios"].append({"url": "https://media.example.com/sfx.wav", "type": "sfx"})
        with (
            mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock, return_value=self.wire(response)) as send,
            mock.patch.object(
                self.media, "download_artifact", new_callable=mock.AsyncMock, return_value=self.audio()
            ) as download,
        ):
            result = (await self.run_node()).result
        self.assertEqual(len(result[0]), 6)
        self.assertIsInstance(result[0][1], self.Blocker)
        self.assertIsInstance(result[0][2], self.Blocker)
        self.assertIsInstance(result[0][3], self.Blocker)
        self.assertIsInstance(result[0][4], self.Blocker)
        self.assertEqual(result[0][5]["sample_rate"], 8000)
        self.assertEqual(result[6], ["speaker", "bgm", "", "", "", "sfx"])
        self.assertEqual(len(result[9]), 6)
        self.assertEqual(json.loads(result[11]), response)
        self.assertEqual(download.await_count, 2)
        send.assert_awaited_once()

    async def test_actual_artifact_decoding_and_temp_cleanup(self):
        payload = self.media.audio_to_wav_bytes(self.audio(channels=2))

        async def chunks(size):
            yield payload

        @contextlib.asynccontextmanager
        async def get(*args, **kwargs):
            yield types.SimpleNamespace(
                content=types.SimpleNamespace(iter_chunked=chunks), raise_for_status=lambda: None
            )

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(self.media.folder_paths, "get_temp_directory", return_value=directory),
            mock.patch.object(self.media, "public_download_response", side_effect=get),
        ):
            audio = await self.media.download_artifact("https://media.example.com/a.wav", "audio")
            self.assertEqual(audio["waveform"].shape, (1, 2, 16000))
            self.assertEqual(audio["sample_rate"], 8000)
            payload = b"\xef\xbb\xbf1\n00:00:00,000 --> 00:00:01,000\nHello.\n"
            subtitle = await self.media.download_artifact("https://media.example.com/a.srt", "subtitle")
            self.assertTrue(subtitle.startswith("1\n"))
            payload = b"not audio"
            with self.assertRaises(self.shared.BytePlusException):
                await self.media.download_artifact("https://media.example.com/broken.wav", "audio")
            self.assertEqual(os.listdir(os.path.join(directory, "BytePlus", "SeedAudio15")), [])

    async def test_interrupt_during_artifact_download_cleans_up_without_retry(self):
        mm = importlib.import_module("comfy.model_management")

        async def chunks(size):
            yield b"partial"
            raise mm.InterruptProcessingException()

        @contextlib.asynccontextmanager
        async def get(*args, **kwargs):
            yield types.SimpleNamespace(
                content=types.SimpleNamespace(iter_chunked=chunks), raise_for_status=lambda: None
            )

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(self.media.folder_paths, "get_temp_directory", return_value=directory),
            mock.patch.object(self.media, "public_download_response", side_effect=get) as download,
            mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock, return_value=self.wire()) as send,
        ):
            with self.assertRaises(mm.InterruptProcessingException):
                await self.run_node()
            self.assertEqual(os.listdir(os.path.join(directory, "BytePlus", "SeedAudio15")), [])
        send.assert_awaited_once()
        download.assert_called_once()

    async def test_echoed_key_is_removed_from_response(self):
        response = self.response(note=self.client.api_key, api_key=self.client.api_key)
        with mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock, return_value=self.wire(response)):
            result = await self.api.generate(self.client, self.build())
        self.assertNotIn(self.client.api_key, json.dumps(result))

    async def test_network_failure_is_not_retried(self):
        aiohttp = importlib.import_module("aiohttp")
        for error in (aiohttp.ClientConnectionError(f"disconnected {self.client.api_key}"), asyncio.TimeoutError()):
            with (
                self.subTest(error=error),
                mock.patch.object(self.api, "_send", new_callable=mock.AsyncMock, side_effect=error) as send,
                self.assertRaises(self.shared.BytePlusException) as caught,
            ):
                await self.run_node()
            self.assertNotIn(self.client.api_key, str(caught.exception))
            send.assert_awaited_once()

    async def test_interrupt_cancels_local_request_and_never_resubmits(self):
        mm = importlib.import_module("comfy.model_management")
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def pending(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with mock.patch.object(self.api, "_send", side_effect=pending) as send:
            task = asyncio.create_task(self.run_node())
            await started.wait()
            mm.interrupt_current_processing(True)
            try:
                with self.assertRaises(mm.InterruptProcessingException):
                    await task
                await asyncio.sleep(0)
            finally:
                mm.interrupt_current_processing(False)
        self.assertTrue(cancelled.is_set())
        send.assert_called_once()

    async def test_pending_interrupt_prevents_billing_after_preprocessing_or_upload(self):
        mm = importlib.import_module("comfy.model_management")
        prepare = self.media.prepare_content

        def interrupted_prepare(options):
            content = prepare(options)
            mm.interrupt_current_processing(True)
            return content

        async def interrupted_upload(*args, **kwargs):
            mm.interrupt_current_processing(True)
            return "https://media.example.com/cached.mp4"

        video = types.SimpleNamespace(
            get_duration=lambda: 4,
            get_frame_rate=lambda: 24,
            get_dimensions=lambda: (1280, 720),
            get_stream_source=lambda: None,
            save_to=lambda buffer, **kwargs: buffer.write(b"encoded video"),
        )
        for stage in ("prepare", "upload"):
            with (
                self.subTest(stage=stage),
                mock.patch.object(
                    self.nodes, "prepare_content", side_effect=interrupted_prepare if stage == "prepare" else prepare
                ),
                mock.patch.object(self.media, "upload_bytes_to_comfy_storage", side_effect=interrupted_upload),
                mock.patch.object(self.api, "send_http_request", new_callable=mock.AsyncMock) as transport,
            ):
                mode = (
                    self.default_mode
                    if stage == "prepare"
                    else {"mode": "video to audio", "text_prompt": "Hello", "video": video}
                )
                try:
                    with self.assertRaises(mm.InterruptProcessingException):
                        await self.run_node(mode)
                finally:
                    mm.interrupt_current_processing(False)
                transport.assert_not_called()

    async def test_send_checks_cancellation_at_transport_boundary(self):
        mm = importlib.import_module("comfy.model_management")
        with mock.patch.object(self.api, "send_http_request", new_callable=mock.AsyncMock) as transport:
            mm.interrupt_current_processing(True)
            try:
                with self.assertRaises(mm.InterruptProcessingException):
                    await self.api._send("https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations", {}, {})
            finally:
                mm.interrupt_current_processing(False)
        transport.assert_not_called()
