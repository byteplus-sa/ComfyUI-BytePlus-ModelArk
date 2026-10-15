"""
BytePlus 3D nodes (Hyper3D Gen2, Hitem3D 2.0): request building, task polling,
archive extraction and outputs. Fake Ark clients only; nothing calls the API.

  COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_3d
"""
import asyncio
import importlib
import io
import os
import shutil
import sys
import tempfile
import types
import unittest
import zipfile
from types import SimpleNamespace
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
    nodes_3d = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_3d")
    nodes_shared = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_shared")
    import torch


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


def make_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def batch(count, channels=3):
    return torch.rand(count, 8, 8, channels)


def hyper_request(**overrides):
    values = dict(
        prompt="a robot", images=None, mesh_mode="Quad", material="PBR", file_format="glb",
        subdivision_level="medium", quality_override=0, hd_texture=None, addons=False, ta_pose=None,
        use_original_alpha=False, bbox=(0, 0, 0), seed=7,
    )
    values.update(overrides)
    return nodes_3d.build_hyper3d_request(**values)


@requires_comfyui
class Hyper3DRequestTests(unittest.TestCase):
    def test_text_request_sends_flags_in_the_prompt_and_seed_as_a_field(self):
        request = hyper_request(hd_texture=True, addons=True, ta_pose=True)
        self.assertEqual(request["model"], "hyper3d-gen2-260112")
        self.assertEqual(request["seed"], 7)
        self.assertEqual(len(request["content"]), 1)
        text = request["content"][0]["text"]
        self.assertTrue(text.startswith("a robot --mesh_mode Quad --material PBR --fileformat glb"))
        for flag in ("--hd_texture true", "--addons HighPack", "--TAPose true", "--subdivisionlevel medium"):
            self.assertIn(flag, text)
        self.assertNotIn("bbox_condition", text)
        self.assertNotIn("use_original_alpha", text)

    def test_quality_override_replaces_subdivision_level(self):
        text = hyper_request(quality_override=50000)["content"][0]["text"]
        self.assertIn("--quality_override 50000", text)
        self.assertNotIn("subdivisionlevel", text)

    def test_face_count_is_checked_against_the_mesh_mode(self):
        with self.assertRaises(nodes_3d.BytePlusException):
            hyper_request(mesh_mode="Quad", quality_override=300000)
        self.assertIn("--quality_override 300000", hyper_request(mesh_mode="Raw", quality_override=300000)["content"][0]["text"])

    def test_bbox_needs_all_three_sizes(self):
        self.assertNotIn("bbox", hyper_request(bbox=(100, 100, 0))["content"][0]["text"])
        self.assertIn("--bbox_condition [100,80,60]", hyper_request(bbox=(100, 80, 60))["content"][0]["text"])

    def test_images_follow_the_text_and_alpha_images_are_png(self):
        request = hyper_request(prompt="", images=batch(2, channels=4), use_original_alpha=True)
        self.assertEqual([item["type"] for item in request["content"]], ["text", "image_url", "image_url"])
        self.assertIn("--use_original_alpha true", request["content"][0]["text"])
        self.assertTrue(request["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        rgb = hyper_request(images=batch(1))
        self.assertTrue(rgb["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_rejects_empty_input_long_prompt_too_many_images_and_flags(self):
        for overrides in (
            dict(prompt="  "), dict(prompt="x" * 401), dict(images=batch(6)), dict(prompt="a robot --material None"),
        ):
            with self.subTest(overrides=overrides), self.assertRaises(nodes_3d.BytePlusException):
                hyper_request(**overrides)


@requires_comfyui
class Hitem3DRequestTests(unittest.TestCase):
    def build(self, images, **overrides):
        values = dict(resolution="1536pro", request_type="Geometry and texture", file_format="glb", face=0, views="")
        values.update(overrides)
        return nodes_3d.build_hitem3d_request(images, **values)

    def test_single_image_request(self):
        request = self.build(batch(1))
        self.assertEqual(request["model"], "hitem3d-2-0-251223")
        self.assertEqual(request["content"][0]["text"], "--resolution 1536pro --request_type 3 --fileformat 2")
        self.assertEqual(request["content"][1]["type"], "image_url")

    def test_geometry_only_and_face_count(self):
        text = self.build(batch(1), request_type="Geometry only", face=500000, file_format="obj")["content"][0]["text"]
        self.assertEqual(text, "--resolution 1536pro --request_type 1 --fileformat 1 --face 500000")

    def test_several_images_default_to_the_first_views(self):
        self.assertIn("--multi_images_bit 1110", self.build(batch(3))["content"][0]["text"])

    def test_explicit_views_must_match_the_image_count(self):
        self.assertIn("--multi_images_bit 1010", self.build(batch(2), views="1010")["content"][0]["text"])
        for views, count in (("10", 2), ("1110", 2), ("10a0", 2), ("0000", 1)):
            with self.subTest(views=views), self.assertRaises(nodes_3d.BytePlusException):
                self.build(batch(count), views=views)

    def test_rejects_no_images_and_five_images(self):
        for images in (None, batch(5)):
            with self.subTest(count=nodes_3d.image_count(images)), self.assertRaises(nodes_3d.BytePlusException):
                self.build(images)


@requires_comfyui
class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)

    def write(self, name, data):
        path = os.path.join(self.folder, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_zip_with_model_and_texture_returns_the_requested_format(self):
        archive = self.write("result.download", make_zip({"a/model.glb": b"glb-data", "a/model.obj": b"o", "a/tex.png": b"p"}))
        model, path = nodes_3d.load_result(archive, self.folder, "t1", "glb")
        self.assertEqual(model.format, "glb")
        self.assertEqual(model.get_bytes(), b"glb-data")
        self.assertTrue(path.endswith(os.path.join("a", "model.glb")))
        self.assertTrue(os.path.exists(os.path.join(self.folder, "a", "tex.png")))

    def test_falls_back_to_any_model_format(self):
        archive = self.write("result.download", make_zip({"m.obj": b"obj", "m.mtl": b"mtl"}))
        model, _ = nodes_3d.load_result(archive, self.folder, "t1", "glb")
        self.assertEqual(model.format, "obj")

    def test_result_file_is_named_after_the_link_extension_or_the_requested_format(self):
        name = nodes_3d.result_file_name
        self.assertEqual(name("https://host/a/b/model.GLB?X-Sig=1", "obj"), "result.glb")
        self.assertEqual(name("https://host/a/b/model.obj?X-Sig=1", "glb"), "result.obj")
        self.assertEqual(name("https://host/a/b/download?X-Sig=1", "fbx"), "result.fbx")
        self.assertEqual(name("https://host/a/model.zip", "glb"), "result.glb")

    def test_plain_file_that_is_not_a_zip_is_used_as_is(self):
        path = self.write("model.glb", b"glTF....")
        model, _ = nodes_3d.load_result(path, self.folder, "t1", "glb")
        self.assertEqual(model.get_bytes(), b"glTF....")

    def test_archive_without_a_model_is_an_error_naming_its_files(self):
        archive = self.write("result.download", make_zip({"readme.txt": b"x"}))
        with self.assertRaises(nodes_3d.BytePlusException) as caught:
            nodes_3d.load_result(archive, self.folder, "t1", "glb")
        self.assertIn("readme.txt", str(caught.exception))

    def test_archive_path_traversal_is_refused(self):
        archive = self.write("result.download", make_zip({"../escape.glb": b"x"}))
        with self.assertRaises(nodes_3d.BytePlusException):
            nodes_3d.load_result(archive, self.folder, "t1", "glb")
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.folder), "escape.glb")))

    def test_oversized_archive_is_refused(self):
        archive = self.write("result.download", make_zip({"m.glb": b"x" * 10}))
        with mock.patch.object(nodes_3d, "MAX_EXTRACTED_BYTES", 5), self.assertRaises(nodes_3d.BytePlusException):
            nodes_3d.extract_archive(archive, self.folder)


def fake_tasks(statuses, file_url="https://example.com/m.zip", error=None, create_error=None):
    """Fake Ark client: tasks.get walks through statuses; records create/delete calls."""
    calls = SimpleNamespace(created=[], deleted=[], polls=0)
    sequence = list(statuses)

    def create(**kwargs):
        if create_error:
            raise create_error
        calls.created.append(kwargs)
        return SimpleNamespace(id="task-1")

    def get(task_id):
        calls.polls += 1
        status = sequence.pop(0) if len(sequence) > 1 else sequence[0]
        if isinstance(status, Exception):
            raise status
        content = {"file_url": file_url} if status == "succeeded" else {}
        return SimpleNamespace(id=task_id, status=status, content=content, error=error)

    def delete(task_id):
        calls.deleted.append(task_id)

    tasks = SimpleNamespace(create=create, get=get, delete=delete)
    ark = SimpleNamespace(content_generation=SimpleNamespace(tasks=tasks))
    return SimpleNamespace(ark=ark, billed_ark=ark), calls


@requires_comfyui
class TaskFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.object(nodes_3d, "THREE_D_POLL_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        for node_cls in (nodes_3d.BytePlusHyper3D, nodes_3d.BytePlusHitem3D):
            hidden = mock.patch.object(node_cls, "hidden", SimpleNamespace(unique_id="9"), create=True)
            hidden.start()
            self.addCleanup(hidden.stop)

    async def test_submit_passes_model_content_and_seed(self):
        client, calls = fake_tasks(["succeeded"])
        task_id = await nodes_3d.submit_task(client, hyper_request())
        self.assertEqual(task_id, "task-1")
        self.assertEqual(calls.created[0]["model"], "hyper3d-gen2-260112")
        self.assertEqual(calls.created[0]["seed"], 7)

    async def test_submit_error_becomes_a_byteplus_error(self):
        client, _ = fake_tasks(["succeeded"], create_error=RuntimeError("boom"))
        with self.assertRaises(nodes_3d.BytePlusException):
            await nodes_3d.submit_task(client, hyper_request())

    async def test_waits_through_queued_and_running_then_returns_the_url(self):
        client, calls = fake_tasks(["queued", "running", "succeeded"])
        url = await nodes_3d.wait_for_task(client, "task-1")
        self.assertEqual(url, "https://example.com/m.zip")
        self.assertEqual(calls.polls, 3)

    async def test_file_url_as_object_is_accepted(self):
        client, _ = fake_tasks(["succeeded"], file_url={"url": "https://example.com/o.zip"})
        self.assertEqual(await nodes_3d.wait_for_task(client, "task-1"), "https://example.com/o.zip")

    async def test_failed_task_reports_the_reason(self):
        client, _ = fake_tasks(["failed"], error=SimpleNamespace(code="Bad", message="no good"))
        with self.assertRaises(nodes_3d.BytePlusException) as caught:
            await nodes_3d.wait_for_task(client, "task-1")
        self.assertIn("no good", str(caught.exception))

    async def test_succeeded_without_a_file_is_an_error(self):
        client, _ = fake_tasks(["succeeded"], file_url=None)
        with self.assertRaises(nodes_3d.BytePlusException):
            await nodes_3d.wait_for_task(client, "task-1")

    async def test_cancelled_and_expired_tasks_end_the_wait(self):
        for status in ("cancelled", "expired"):
            client, _ = fake_tasks([status])
            with self.subTest(status=status), self.assertRaises(nodes_3d.BytePlusException):
                await nodes_3d.wait_for_task(client, "task-1")

    async def test_transient_poll_errors_are_retried_and_permanent_ones_are_not(self):
        client, calls = fake_tasks([RuntimeError("blip"), "succeeded"])
        await nodes_3d.wait_for_task(client, "task-1")
        self.assertEqual(calls.polls, 2)
        permanent = RuntimeError("revoked")
        permanent.status_code = 401
        client, calls = fake_tasks([permanent])
        with self.assertRaises(nodes_3d.BytePlusException):
            await nodes_3d.wait_for_task(client, "task-1")
        self.assertEqual(calls.polls, 1)

    async def test_interrupt_deletes_the_task_and_reraises(self):
        client, calls = fake_tasks(["running"])
        interrupt = nodes_3d.comfy.model_management.InterruptProcessingException
        with mock.patch.object(
            nodes_3d.comfy.model_management, "throw_exception_if_processing_interrupted",
            side_effect=[None, interrupt()],
        ), self.assertRaises(interrupt):
            await nodes_3d.wait_for_task(client, "task-1")
        self.assertEqual(calls.deleted, ["task-1"])

    async def test_node_returns_the_model_json_and_a_preview(self):
        client, calls = fake_tasks(["running", "succeeded"])
        temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp, True)
        archive = make_zip({"robot.glb": b"glb-data", "tex.png": b"p"})

        async def fake_download(url, destination, task_id):
            with open(destination, "wb") as handle:
                handle.write(archive)

        with mock.patch.object(nodes_3d.folder_paths, "get_temp_directory", return_value=temp), \
                mock.patch.object(nodes_3d, "download_result", fake_download):
            output = await nodes_3d.BytePlusHyper3D.execute(client, prompt="a robot", seed=3)
        self.assertIsNone(output.ui)
        self.assertEqual(output.args[0].format, "glb")
        self.assertEqual(output.args[0].get_bytes(), b"glb-data")
        self.assertIn('"task_id": "task-1"', output.args[1])

    async def test_node_accepts_a_plain_model_file_named_by_the_link(self):
        client, _ = fake_tasks(["succeeded"], file_url="https://example.com/out/model.obj?sig=1")
        temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp, True)

        async def fake_download(url, destination, task_id):
            with open(destination, "wb") as handle:
                handle.write(b"# Blender\no model\n")

        with mock.patch.object(nodes_3d.folder_paths, "get_temp_directory", return_value=temp), \
                mock.patch.object(nodes_3d, "download_result", fake_download):
            output = await nodes_3d.BytePlusHyper3D.execute(client, prompt="a chair", file_format="glb")
        self.assertEqual(output.args[0].format, "obj")
        self.assertEqual(output.args[0].get_bytes(), b"# Blender\no model\n")

    async def test_download_failure_removes_the_temp_folder_and_names_the_task(self):
        client, _ = fake_tasks(["succeeded"])
        temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp, True)

        async def broken_download(url, destination, task_id):
            raise nodes_3d.BytePlusException("[BytePlus] download failed task-1")

        with mock.patch.object(nodes_3d.folder_paths, "get_temp_directory", return_value=temp), \
                mock.patch.object(nodes_3d, "download_result", broken_download), \
                self.assertRaises(nodes_3d.BytePlusException):
            await nodes_3d.BytePlusHitem3D.execute(client, batch(1))
        self.assertEqual(os.listdir(os.path.join(temp, "BytePlus", "3d")), [])


if __name__ == "__main__":
    unittest.main()
