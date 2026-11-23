import importlib
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest import mock

COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
requires_comfyui = unittest.skipUnless(COMFY_ROOT, "Set COMFYUI_ROOT to run LAS media tests.")

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
    las_media = importlib.import_module(f"{PACKAGE_NAME}.nodes.las_media")
    from comfy_execution.graph_utils import ExecutionBlocker


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


@requires_comfyui
class SourceTests(unittest.TestCase):
    def test_exactly_one_source_is_required(self):
        with self.assertRaises(Exception):
            las_media.check_source(None, "", "video")
        with self.assertRaises(Exception):
            las_media.check_source(object(), "https://example.com/video.mp4", "video")
        self.assertEqual(las_media.check_source(None, "tos://bucket/video.mp4", "video"), "tos://bucket/video.mp4")

    def test_private_and_credentialed_download_urls_are_rejected(self):
        for url in (
            "http://127.0.0.1/a",
            "http://[::1]/a",
            "http://10.1.1.1/a",
            "http://169.254.169.254/a",
            "https://user:secret@example.com/a",
            "file:///tmp/a",
            "http://localhost/a",
            "http://192.168.1.1/a",
        ):
            with self.subTest(url=url), self.assertRaises(Exception):
                las_media.check_download_url(url)
        self.assertEqual(
            las_media.check_download_url("https://example.com/a?signature=test"), "https://example.com/a?signature=test"
        )

    def test_translation_limits_include_audio_and_restoration(self):
        base = {"duration": 2.0, "fps": 30.0, "width": 128, "height": 128, "size": 1, "has_audio": True}
        las_media.validate_translation_metadata(base)
        for change in ({"duration": 1.99}, {"duration": 14400.01}, {"size": 10_000_000_001}, {"has_audio": False}):
            with self.subTest(change=change), self.assertRaises(Exception):
                las_media.validate_translation_metadata({**base, **change})
        las_media.validate_translation_metadata({**base, "has_audio": False}, external_audio=True, lip_sync=True)
        for change in ({"duration": 600.01}, {"fps": 30.01}):
            with self.subTest(change=change), self.assertRaises(Exception):
                las_media.validate_translation_metadata({**base, **change}, lip_sync=True)
        for change in ({"duration": 10800.01}, {"width": 3841}, {"height": 2161, "width": 3840}):
            with self.subTest(change=change), self.assertRaises(Exception):
                las_media.validate_translation_metadata({**base, **change}, restoration=True)

    def test_audio_batch_is_split_or_rejected_explicitly(self):
        import torch

        audio = {"waveform": torch.zeros(2, 1, 160), "sample_rate": 16000}
        self.assertEqual(len(las_media.split_audio(audio)), 2)
        with self.assertRaises(Exception):
            las_media.split_audio(audio, single=True)
        for invalid in (None, {}, {"waveform": torch.zeros(1, 1, 0), "sample_rate": 16000}):
            with self.subTest(invalid=type(invalid)), self.assertRaises(Exception):
                las_media.split_audio(invalid)

    def test_video_metadata_preserves_trim_crop_and_video_stream_duration(self):
        import av
        import numpy

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "source.mp4")
            with av.open(path, "w") as container:
                stream = container.add_stream("libx264", rate=24)
                stream.width, stream.height, stream.pix_fmt = 16, 16, "yuv420p"
                audio_stream = container.add_stream("aac", rate=16000)
                audio_stream.layout = "mono"
                for _ in range(72):
                    frame = av.VideoFrame.from_ndarray(numpy.zeros((16, 16, 3), dtype=numpy.uint8), format="rgb24")
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
                audio = av.AudioFrame.from_ndarray(
                    numpy.zeros((1, 64000), dtype=numpy.float32), format="fltp", layout="mono"
                )
                audio.sample_rate = 16000
                for packet in audio_stream.encode(audio):
                    container.mux(packet)
                for packet in audio_stream.encode():
                    container.mux(packet)
            source = las_media.VideoFromFile(path)
            self.assertAlmostEqual(las_media.video_metadata(source)["duration"], 3.0)
            trimmed = source.as_trimmed(0.5, 2.0, strict_duration=True)
            cropped = trimmed.as_cropped(0, 0, 8, 8)
            metadata = las_media.video_metadata(cropped)
            self.assertAlmostEqual(metadata["duration"], 2.0)
            self.assertEqual((metadata["width"], metadata["height"]), (8, 8))
            self.assertTrue(metadata["has_audio"])

    def test_timeline_alignment_accepts_one_second_but_rejects_more(self):
        las_media.validate_alignment({"duration": 2.0}, {"duration": 3.0}, "Audio")
        with self.assertRaises(Exception):
            las_media.validate_alignment({"duration": 2.0}, {"duration": 3.01}, "Audio")


@requires_comfyui
class CollectionTests(unittest.IsolatedAsyncioTestCase):
    def task(self, assets):
        return {"metadata": {"task_id": "test-task", "task_status": "COMPLETED"}, "data": {"translated_videos": assets}}

    async def test_language_order_and_missing_outputs_remain_aligned(self):
        task = self.task(
            [
                {"language": "ja-JP", "type": "translated_video", "presigned_url": "https://example.com/ja"},
                {"language": "es-MX", "type": "translated_video", "presigned_url": "https://example.com/es"},
            ]
        )

        async def download(asset, task_id):
            return asset["language"], {"download_status": "downloaded", "local_path": "/tmp/test"}

        with mock.patch.object(las_media, "_collect_asset", side_effect=download):
            result = await las_media.collect_translation(task, languages=["es-MX", "ja-JP"], download_captions=False)
        self.assertEqual(result.values[0], ["es-MX", "ja-JP"])
        self.assertEqual(result.values[3], ["es-MX", "ja-JP"])
        self.assertTrue(all(isinstance(value, ExecutionBlocker) for value in result.values[1]))

    async def test_partial_download_failure_does_not_discard_other_video(self):
        task = self.task(
            [
                {"language": "es-MX", "type": "translated_video", "presigned_url": "https://example.com/es"},
                {"language": "ja-JP", "type": "translated_video", "presigned_url": "https://example.com/ja"},
            ]
        )

        async def download(asset, task_id):
            if asset["language"] == "ja-JP":
                raise ValueError("bad media")
            return "video", {"download_status": "downloaded", "local_path": "/tmp/test"}

        with mock.patch.object(las_media, "_collect_asset", side_effect=download):
            result = await las_media.collect_translation(task, languages=["es-MX", "ja-JP"], download_captions=False)
        self.assertEqual(result.values[0][0], "video")
        self.assertIsInstance(result.values[0][1], ExecutionBlocker)
        self.assertFalse(result.complete)

    async def test_duplicate_video_is_not_arbitrarily_chosen(self):
        asset = {"language": "es-MX", "type": "translated_video", "presigned_url": "https://example.com/es"}
        with mock.patch.object(las_media, "_collect_asset") as download:
            result = await las_media.collect_translation(
                self.task([asset, asset]), languages=["es-MX"], download_captions=False
            )
        download.assert_not_called()
        self.assertIsInstance(result.values[0][0], ExecutionBlocker)
        self.assertFalse(result.complete)

    async def test_missing_requested_audio_and_repaired_video_preserve_recovery(self):
        asset = {"language": "es-MX", "type": "translated_video", "presigned_url": "https://example.com/es"}
        with mock.patch.object(las_media, "_collect_asset", return_value=("video", {})):
            result = await las_media.collect_translation(
                self.task([asset]),
                languages=["es-MX"],
                download_captions=False,
                require_audio=True,
                require_repaired_video=True,
            )
        self.assertEqual(result.values[0], ["video"])
        self.assertIsInstance(result.values[1][0], ExecutionBlocker)
        self.assertIsInstance(result.values[4], ExecutionBlocker)
        self.assertFalse(result.complete)

    async def test_bom_caption_and_audio_file_decoding(self):
        import torch
        from byteplus_plugin_test.nodes.audio_utils import audio_to_wav_bytes

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "audio.wav")
            with open(path, "wb") as handle:
                handle.write(audio_to_wav_bytes({"waveform": torch.zeros(1, 1, 160), "sample_rate": 16000}))
            decoded = las_media.decode_audio_file(path)
            self.assertEqual(decoded["waveform"].shape, (1, 1, 160))
            self.assertEqual(decoded["sample_rate"], 16000)
        self.assertEqual(las_media.decode_caption(b"\xef\xbb\xbfhello"), "hello")

    async def test_private_redirect_is_checked_before_following(self):
        class Response:
            status = 302
            headers = {"Location": "http://127.0.0.1/private"}

            def raise_for_status(self):
                pass

        class Context:
            async def __aenter__(self):
                return Response()

            async def __aexit__(self, *args):
                pass

        session = SimpleNamespace(get=mock.Mock(return_value=Context()))
        with self.assertRaises(Exception):
            async with las_media.safe_get(session, "https://example.com/a"):
                pass
        self.assertEqual(session.get.call_count, 1)
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
