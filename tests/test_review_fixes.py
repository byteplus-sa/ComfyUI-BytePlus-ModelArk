"""
Fixes from the live end-to-end run: console traceback for pack errors on Python 3.13,
doubled full stops in messages, Video Query Tasks with an unknown task ID, the audio
format of a link without a file extension (Seed Audio url into Seed ASR), and the new
hints (running tasks cannot be cancelled, long waits, intermittent content filter).

  COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_review_fixes
"""
import asyncio
import importlib
import importlib.util
import json
import os
import sys
import traceback
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

    constants = importlib.import_module(f"{PACKAGE_NAME}.nodes.constants")
    core_style = importlib.import_module(f"{PACKAGE_NAME}.nodes.core_style")
    nodes_shared = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_shared")
    nodes_speech = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_speech")
    nodes_video = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_video")


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


def run(coroutine):
    return asyncio.run(coroutine)


class FakeTask:
    def __init__(self, task_id):
        self.id = task_id

    def model_dump(self):
        return {"id": self.id, "status": "succeeded"}


class FakeTasks:
    def __init__(self, known):
        self.known = known

    def list(self, **kwargs):
        wanted = kwargs.get("task_ids") or list(self.known)
        items = [FakeTask(t) for t in wanted if t in self.known]
        return SimpleNamespace(items=items, total=len(items))


def fake_client(known):
    ark = SimpleNamespace(content_generation=SimpleNamespace(tasks=FakeTasks(known)))
    return SimpleNamespace(ark=ark)


@requires_comfyui
class TracebackSuppressionTests(unittest.TestCase):
    """__init__.py patches traceback.format_exception; ComfyUI reaches it as format_exc()."""

    @classmethod
    def setUpClass(cls):
        saved = (traceback.print_exception, traceback.format_exception)
        import logging

        saved_logging_error = logging.error
        spec = importlib.util.spec_from_file_location(
            "byteplus_init_under_test",
            os.path.join(PLUGIN_ROOT, "__init__.py"),
            submodule_search_locations=[PLUGIN_ROOT],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
            cls.patched_format_exception = staticmethod(traceback.format_exception)
        finally:
            traceback.print_exception, traceback.format_exception = saved
            logging.error = saved_logging_error
            sys.modules.pop(spec.name, None)
        cls.module = module

    def make_exception(self, suppress):
        try:
            raise nodes_shared.BytePlusException("[BytePlus] text is empty.")
        except nodes_shared.BytePlusException as exc:
            exc.byteplus_suppress_traceback = suppress
            return exc

    def test_modern_call_form_prints_only_the_message(self):
        exc = self.make_exception(True)
        lines = self.patched_format_exception(exc, limit=None, chain=True)
        self.assertEqual(lines, ["[BytePlus] text is empty.\n"])

    def test_legacy_call_form_prints_only_the_message(self):
        exc = self.make_exception(True)
        lines = self.patched_format_exception(type(exc), exc, exc.__traceback__)
        self.assertEqual(lines, ["[BytePlus] text is empty.\n"])

    def test_unmarked_exceptions_keep_their_traceback(self):
        exc = self.make_exception(False)
        lines = self.patched_format_exception(exc)
        self.assertTrue(any("Traceback" in line for line in lines))


@requires_comfyui
class MessageTests(unittest.TestCase):
    def test_upload_error_has_one_full_stop(self):
        text = nodes_shared.get_text(
            "err_comfy_upload_failed_reference", e="Unauthorized: Please login first to use this node."
        )
        self.assertNotIn("..", text)
        self.assertIn("this node. Log in", text)

    def test_get_text_collapses_a_doubled_full_stop_but_keeps_ellipses(self):
        with mock.patch.dict(constants.MESSAGES, {"probe": "Failed: {e}. Log in.", "dots": "Waiting... done"}):
            self.assertEqual(nodes_shared.get_text("probe", e="No access."), "Failed: No access. Log in.")
            self.assertEqual(nodes_shared.get_text("dots"), "Waiting... done")

    def test_new_hints_exist(self):
        for key in (
            "err_query_tasks_not_found", "query_tasks_some_not_found", "cancel_running_billed",
            "node_poll_long_wait", "seed_audio_no_subtitles",
        ):
            self.assertIn(key, constants.MESSAGES)
        errors = constants.MESSAGES["api_errors"]
        self.assertIn("billed", errors["TaskRunningCannotCancel"])
        for code in (
            "OutputImageSensitiveContentDetected", "OutputVideoSensitiveContentDetected",
            "OutputAudioSensitiveContentDetected",
        ):
            self.assertIn("another seed", errors[code])
        self.assertIn("reference, edit or extend", errors["InvalidParameter.TaskTypeConstraint"])


@requires_comfyui
class QueryTasksTests(unittest.TestCase):
    def query(self, known, task_ids):
        node = nodes_video.BytePlusVideoQueryTasks
        return run(node.execute(fake_client(known), 1, 10, "all", "", task_ids, "all", 0))

    def test_unknown_task_id_is_an_error(self):
        with self.assertRaises(nodes_shared.BytePlusException) as caught:
            self.query({"cgt-1": None}, "cgt-unknown")
        self.assertIn("cgt-unknown", str(caught.exception))
        self.assertIn("[BytePlus]", str(caught.exception))

    def test_known_task_id_is_returned(self):
        result = self.query({"cgt-1": None}, "cgt-1")
        self.assertEqual(json.loads(result.args[0])[0]["id"], "cgt-1")
        self.assertEqual(result.args[1], 1)

    def test_partly_unknown_ids_return_the_known_ones(self):
        result = self.query({"cgt-1": None}, "cgt-1\ncgt-unknown")
        self.assertEqual([t["id"] for t in json.loads(result.args[0])], ["cgt-1"])

    def test_listing_without_ids_is_unchanged(self):
        result = self.query({"cgt-1": None, "cgt-2": None}, "")
        self.assertEqual(len(json.loads(result.args[0])), 2)


@requires_comfyui
class AudioFormatTests(unittest.TestCase):
    def test_magic_bytes(self):
        sniff = core_style.audio_format_from_bytes
        self.assertEqual(sniff(b"RIFF\x24\x00\x00\x00WAVEfmt "), "wav")
        self.assertEqual(sniff(b"ID3\x04\x00\x00\x00\x00\x00\x00"), "mp3")
        self.assertEqual(sniff(b"\xff\xfb\x90\x00" + b"\x00" * 12), "mp3")
        self.assertEqual(sniff(b"OggS\x00\x02" + b"\x00" * 10), "ogg")
        self.assertEqual(sniff(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 4), "m4a")
        self.assertEqual(sniff(b"#!AMR\n" + b"\x00" * 10), "amr")
        self.assertIsNone(sniff(b"<html>"))
        self.assertIsNone(sniff(b""))
        self.assertIsNone(sniff(None))

    def test_http_links_are_not_fetched(self):
        # Only public https hosts are probed; nothing is requested for plain http.
        self.assertIsNone(run(core_style.sniff_audio_format("http://example.com/audio")))
        self.assertIsNone(run(core_style.sniff_audio_format("https://localhost/audio")))


if __name__ == "__main__":
    unittest.main()
