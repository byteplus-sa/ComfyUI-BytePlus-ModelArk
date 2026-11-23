import importlib
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
requires_comfyui = unittest.skipUnless(COMFY_ROOT, "Set COMFYUI_ROOT to run LAS node tests.")

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
    nodes_las = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_las")
    las_api = importlib.import_module(f"{PACKAGE_NAME}.nodes.las_api")
    from comfy_execution.graph_utils import ExecutionBlocker


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


@requires_comfyui
class SchemaTests(unittest.TestCase):
    def test_three_nodes_register_without_key_or_client_inputs(self):
        self.assertEqual(len(nodes_las.NODES), 3)
        for node in nodes_las.NODES:
            info = node.GET_NODE_INFO_V1()
            inputs = {**info["input"]["required"], **info["input"].get("optional", {})}
            self.assertEqual(info["category"], "BytePlus ModelArk/LAS")
            self.assertFalse(info["output_node"])
            self.assertFalse(set(inputs) & {"api_key", "client", "las_client"})

    def test_translation_has_native_modes_and_aligned_list_outputs(self):
        info = nodes_las.BytePlusLASVideoTranslation.GET_NODE_INFO_V1()
        modes = info["input"]["required"]["mode"]
        self.assertEqual(modes[0], "COMFY_DYNAMICCOMBO_V3")
        self.assertEqual(
            [item["key"] for item in modes[1]["options"]],
            ["translate", "lip_sync_external_audio", "lip_sync_source_audio"],
        )
        translate_inputs = modes[1]["options"][0]["inputs"]["required"]
        self.assertEqual(translate_inputs["dubbing_mode"][1]["default"], "high_quality")
        self.assertEqual(info["input"]["optional"]["dubbing_audio"][0], "AUDIO")
        self.assertNotIn("dubbing_audio", modes[1]["options"][1]["inputs"])
        self.assertEqual(info["output_is_list"], [True, True, True, True, False, False, False, False])
        self.assertEqual(
            info["output_name"],
            ["video", "audio", "subtitle", "language", "repaired_video", "artifacts_json", "task_id", "response"],
        )

    def test_task_collector_always_rechecks_status(self):
        self.assertNotEqual(
            nodes_las.BytePlusLASTaskResult.fingerprint_inputs(), nodes_las.BytePlusLASTaskResult.fingerprint_inputs()
        )

    def test_generation_fingerprint_rechecks_uncollected_tasks_and_changed_credentials(self):
        for node in nodes_las.NODES[:2]:
            with (
                mock.patch.object(node, "hidden", SimpleNamespace(unique_id="test-node")),
                mock.patch.object(nodes_las.credentials, "get_setting", return_value="test-key-one") as setting,
                mock.patch.dict(las_api.LAS_TASK_HANDLES, {}, clear=True),
            ):
                original = node.fingerprint_inputs()
                self.assertEqual(original, node.fingerprint_inputs())
                setting.return_value = "test-key-two"
                self.assertNotEqual(original, node.fingerprint_inputs())
                las_api.LAS_TASK_HANDLES[("test-node", "fingerprint", "operator")] = {}
                self.assertNotEqual(node.fingerprint_inputs(), node.fingerprint_inputs())


@requires_comfyui
class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for node in nodes_las.NODES:
            patch = mock.patch.object(node, "hidden", SimpleNamespace(unique_id="test-node"))
            patch.start()
            self.addCleanup(patch.stop)

    async def test_missing_key_stops_before_upload_or_submit(self):
        with (
            mock.patch.object(nodes_las, "upload_video") as upload,
            mock.patch.object(nodes_las, "submit_and_wait") as submit,
        ):
            with self.assertRaises(Exception):
                await nodes_las.BytePlusLASGenjutsu.execute(
                    user_prompt="edit", template={"template": "basic"}, video_url="tos://bucket/video.mp4"
                )
        upload.assert_not_called()
        submit.assert_not_called()

    async def test_invalid_options_stop_before_connected_media_upload(self):
        client = SimpleNamespace(api_key="fake-test-key", region="ap-southeast-1")
        with (
            mock.patch.object(nodes_las, "upload_video") as upload,
            mock.patch.object(nodes_las, "submit_and_wait") as submit,
        ):
            with self.assertRaises(Exception):
                await nodes_las.BytePlusLASVideoTranslation.execute(
                    client,
                    mode={
                        "mode": "translate",
                        "audio_language": "en-US",
                        "output_languages": "es-MX",
                        "enable_fast_mode": True,
                    },
                    output_tos_path="tos://bucket/output/",
                    video=object(),
                )
        upload.assert_not_called()
        submit.assert_not_called()

    async def test_connected_external_audio_is_rejected_in_translation_mode_before_upload(self):
        client = SimpleNamespace(api_key="fake-test-key", region="ap-southeast-1")
        with mock.patch.object(nodes_las, "upload_audio") as upload:
            with self.assertRaises(Exception):
                await nodes_las.BytePlusLASVideoTranslation.execute(
                    client,
                    mode={"mode": "translate", "audio_language": "en-US", "output_languages": "es-MX"},
                    output_tos_path="tos://bucket/output/",
                    video_url="tos://bucket/video.mp4",
                    dubbing_audio=object(),
                )
        upload.assert_not_called()

    async def test_poll_once_pending_task_does_not_submit(self):
        client = SimpleNamespace(api_key="fake-test-key", region="ap-southeast-1")
        task = {"metadata": {"task_id": "test-task", "task_status": "RUNNING", "business_code": "0"}}
        with (
            mock.patch.object(nodes_las, "poll_las_task", return_value=task),
            mock.patch.object(nodes_las, "submit_and_wait") as submit,
        ):
            result = await nodes_las.BytePlusLASTaskResult.execute(
                client, operator="Video Translation", task_id="test-task", wait_for_completion=False
            )
        submit.assert_not_called()
        self.assertIsInstance(result.result[0][0], ExecutionBlocker)
        self.assertEqual(result.result[6], "test-task")

    async def test_translation_success_preserves_language_lists_and_clears_handle(self):
        client = SimpleNamespace(api_key="fake-test-key", region="ap-southeast-1")
        values = (
            ["video"],
            [ExecutionBlocker(None)],
            ["caption"],
            ["es-MX"],
            ExecutionBlocker(None),
            "{}",
            "test-task",
            "{}",
        )
        with (
            mock.patch.object(
                nodes_las,
                "submit_and_wait",
                return_value={"metadata": {"task_id": "test-task", "task_status": "COMPLETED"}},
            ),
            mock.patch.object(
                nodes_las, "collect_translation", return_value=SimpleNamespace(values=values, complete=True)
            ),
            mock.patch.object(nodes_las, "clear_task_handle") as clear,
        ):
            result = await nodes_las.BytePlusLASVideoTranslation.execute(
                client,
                mode={"mode": "translate", "audio_language": "en-US", "output_languages": "es-MX"},
                output_tos_path="tos://bucket/output/",
                video_url="tos://bucket/video.mp4",
            )
        self.assertEqual(result.result[3], ["es-MX"])
        clear.assert_called_once()
