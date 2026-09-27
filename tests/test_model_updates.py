import asyncio
import importlib
import io
import json
import os
import sys
import types
import unittest
from types import SimpleNamespace


# Needs a ComfyUI checkout and a Python env with torch and the BytePlus SDK:
#   COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_model_updates
COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
requires_comfyui = unittest.skipUnless(
    COMFY_ROOT, "Set COMFYUI_ROOT to a ComfyUI checkout to run these tests."
)

if COMFY_ROOT:
    if COMFY_ROOT not in sys.path:
        sys.path.insert(0, COMFY_ROOT)

    # ComfyUI owns the top-level ``utils`` package. Preloading it prevents the
    # plugin's nodes/utils.py from shadowing ComfyUI when tests run from this repo.
    import utils  # noqa: F401, E402

    PACKAGE_NAME = "byteplus_plugin_test"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [PLUGIN_ROOT]
        sys.modules[PACKAGE_NAME] = package

    models_config = importlib.import_module(f"{PACKAGE_NAME}.nodes.models_config")
    constants = importlib.import_module(f"{PACKAGE_NAME}.nodes.constants")
    executor = importlib.import_module(f"{PACKAGE_NAME}.nodes.executor")
    nodes_image = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_image")
    nodes_video = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_video")
    nodes_shared = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_shared")


@requires_comfyui
class ModelConfigurationTests(unittest.TestCase):
    def test_byteplus_model_ids_and_defaults(self):
        self.assertEqual(
            models_config.SEEDREAM_5_MODEL_MAP["dola-seedream-5-0-pro"],
            "dola-seedream-5-0-pro-260628",
        )
        self.assertEqual(
            models_config.SEEDREAM_5_MODEL_MAP["seedream-5-0-lite"],
            "seedream-5-0-260128",
        )
        self.assertEqual(
            models_config.VIDEO_MODEL_MAP["dreamina-seedance-2-0-mini"],
            "dreamina-seedance-2-0-mini-260615",
        )
        self.assertEqual(
            models_config.VIDEO_MODEL_MAP["dreamina-seedance-2-5"],
            "dreamina-seedance-2-5-260628",
        )
        self.assertEqual(
            next(iter(models_config.VISUAL_MODEL_MAP.items())),
            ("dola-seed-2-1-turbo", "dola-seed-2-1-turbo-260628"),
        )

    def test_no_volcengine_models_remain(self):
        all_ids = [
            *models_config.SEEDREAM_4_MODEL_MAP.items(),
            *models_config.SEEDREAM_5_MODEL_MAP.items(),
            *models_config.VIDEO_MODEL_MAP.items(),
            *models_config.VISUAL_MODEL_MAP.items(),
        ]
        for ui_name, model_id in all_ids:
            self.assertNotIn("doubao", ui_name)
            self.assertNotIn("doubao", model_id)
        self.assertNotIn("seedance-1-0-lite", str(models_config.VIDEO_MODEL_MAP))

    def test_region_endpoints(self):
        self.assertEqual(
            constants.REGION_BASE_URLS[constants.DEFAULT_REGION],
            "https://ark.ap-southeast.bytepluses.com/api/v3",
        )
        self.assertEqual(
            constants.REGION_BASE_URLS["eu-west-1"],
            "https://ark.eu-west.bytepluses.com/api/v3",
        )
        schema = nodes_shared.BytePlusAPIClient.define_schema()
        region_input = next(item for item in schema.inputs if item.id == "region")
        self.assertEqual(region_input.options, ["ap-southeast-1", "eu-west-1"])
        self.assertEqual(region_input.default, "ap-southeast-1")

    def test_seedance_resolution_matrix(self):
        self.assertEqual(
            models_config.VIDEO_2_MODEL_RESOLUTIONS["dreamina-seedance-2-0"],
            ["480p", "720p", "1080p", "4k"],
        )
        for model in ("dreamina-seedance-2-0-fast", "dreamina-seedance-2-0-mini"):
            self.assertEqual(
                models_config.VIDEO_2_MODEL_RESOLUTIONS[model], ["480p", "720p"]
            )
        self.assertEqual(
            nodes_video.validate_seedance2_resolution(
                "dreamina-seedance-2-0", "4k"
            ),
            "4k",
        )
        for model in ("dreamina-seedance-2-0-fast", "dreamina-seedance-2-0-mini"):
            with self.assertRaises(Exception):
                nodes_video.validate_seedance2_resolution(model, "4k")
        self.assertEqual(
            models_config.VIDEO_2_MODEL_RESOLUTIONS["dreamina-seedance-2-5"],
            ["480p", "720p", "1080p"],
        )
        self.assertEqual(
            nodes_video.validate_seedance2_resolution("dreamina-seedance-2-5", "1080p"),
            "1080p",
        )
        with self.assertRaises(Exception):
            nodes_video.validate_seedance2_resolution("dreamina-seedance-2-5", "4k")

    def test_seedance25_duration_and_reference_limits(self):
        self.assertEqual(
            nodes_video.validate_seedance2_duration("dreamina-seedance-2-5", 30),
            30,
        )
        self.assertEqual(
            nodes_video.validate_seedance2_duration(
                "dreamina-seedance-2-5", 30, auto_duration=True
            ),
            -1,
        )
        with self.assertRaises(Exception):
            nodes_video.validate_seedance2_duration("dreamina-seedance-2-5", 31)
        with self.assertRaises(Exception):
            nodes_video.validate_seedance2_duration("dreamina-seedance-2-0", 16)
        with self.assertRaises(Exception):
            nodes_video.validate_seedance2_duration("dreamina-seedance-2-5", 4.5)

        accepted = nodes_video.validate_seedance2_reference_counts(
            "dreamina-seedance-2-5",
            [object()] * 30,
            [object()] * 10,
            [object()] * 10,
        )
        self.assertEqual(accepted, {"images": 30, "videos": 10, "audios": 10})
        with self.assertRaises(Exception):
            nodes_video.validate_seedance2_reference_counts(
                "dreamina-seedance-2-0", [object()] * 10, [], []
            )

    def test_dynamic_combo_schema_and_nested_order(self):
        image_combo = nodes_image.BytePlusSeedream5.define_schema().inputs[1]
        video_combo = nodes_video.BytePlusSeedance2.define_schema().inputs[1]
        self.assertEqual(image_combo.io_type, "COMFY_DYNAMICCOMBO_V3")
        self.assertEqual(video_combo.io_type, "COMFY_DYNAMICCOMBO_V3")
        self.assertEqual(image_combo.options[0].key, "dola-seedream-5-0-pro")
        self.assertEqual(video_combo.options[0].key, "dreamina-seedance-2-0")

        seedance25 = next(
            option
            for option in video_combo.options
            if option.key == "dreamina-seedance-2-5"
        )
        duration_input = next(
            item for item in seedance25.inputs if item.id == "duration"
        )
        self.assertEqual(duration_input.max, 30)

        video_schema = nodes_video.BytePlusSeedance2.define_schema()
        dynamic_inputs = {item.id: item for item in video_schema.inputs}
        self.assertEqual(len(dynamic_inputs["ref_images"].template.names), 30)
        self.assertEqual(len(dynamic_inputs["ref_videos"].template.names), 10)
        self.assertEqual(len(dynamic_inputs["ref_audios"].template.names), 10)

        lite = next(
            option
            for option in image_combo.options
            if option.key == "seedream-5-0-lite"
        )
        self.assertEqual(
            [item.id for item in lite.inputs],
            [
                "prompt",
                "size",
                "width",
                "height",
                "seed",
                "enable_group_generation",
                "max_images",
                "generation_count",
                "watermark",
            ],
        )

        pro = image_combo.options[0]
        size_input = next(item for item in pro.inputs if item.id == "size")
        self.assertEqual(
            size_input.options[:3], ["1K (adaptive)", "1.5K (adaptive)", "2K (adaptive)"]
        )
        self.assertEqual(
            [item.id for item in pro.inputs][-5:],
            ["generation_count", "prompt_optimization", "output_format", "background", "watermark"],
        )

        seedream4_ids = [
            item.id for item in nodes_image.BytePlusSeedream4.define_schema().inputs
        ]
        self.assertEqual(
            seedream4_ids[1:12],
            [
                "model_version",
                "prompt",
                "size",
                "width",
                "height",
                "seed",
                "enable_group_generation",
                "max_images",
                "generation_count",
                "prompt_optimization",
                "watermark",
            ],
        )
        seedream4_optimization = next(
            item
            for item in nodes_image.BytePlusSeedream4.define_schema().inputs
            if item.id == "prompt_optimization"
        )
        self.assertEqual(seedream4_optimization.options, ["standard", "fast"])
        self.assertEqual(seedream4_optimization.default, "standard")

    def test_seedance25_family_options(self):
        video_combo = nodes_video.BytePlusSeedance2.define_schema().inputs[1]
        options = {option.key: [item.id for item in option.inputs] for option in video_combo.options}
        family_inputs = [
            "task_type", "output_format", "draft_mode", "reuse_last_draft_task", "draft_task_id",
        ]
        for model in ("dreamina-seedance-2-5", "dreamina-seedance-2-5-premium"):
            ids = options[model]
            self.assertEqual(ids[ids.index("generate_audio") + 1 :][:5], family_inputs)
        for model, ids in options.items():
            self.assertNotIn("enable_web_search", ids)
            if model not in ("dreamina-seedance-2-5", "dreamina-seedance-2-5-premium"):
                for name in family_inputs:
                    self.assertNotIn(name, ids)

    def test_seedance25_premium(self):
        self.assertEqual(
            models_config.VIDEO_MODEL_MAP["dreamina-seedance-2-5-premium"],
            "dreamina-seedance-2-5-premium-260915",
        )
        self.assertEqual(
            nodes_video.validate_seedance2_resolution("dreamina-seedance-2-5-premium", "4k"),
            "4k",
        )
        self.assertEqual(
            nodes_video.validate_seedance2_duration("dreamina-seedance-2-5-premium", 30),
            30,
        )
        self.assertEqual(
            models_config.VIDEO_2_MODEL_REFERENCE_LIMITS["dreamina-seedance-2-5-premium"],
            {"images": 30, "videos": 10, "audios": 10},
        )
        self.assertIn("dreamina-seedance-2-5-premium", models_config.QUERY_TASKS_MODEL_LIST)

    def test_seed_visual_models(self):
        for name, model_id in (
            ("seed-1-8", "seed-1-8-251228"),
            ("seed-1-6", "seed-1-6-250915"),
            ("seed-1-6-flash", "seed-1-6-flash-250715"),
        ):
            self.assertEqual(models_config.VISUAL_MODEL_MAP[name], model_id)


@requires_comfyui
class RequestAndEstimationTests(unittest.TestCase):
    def test_64_mib_exact_boundary(self):
        limit = constants.SEEDANCE_REQUEST_MAX_BYTES
        overhead = executor.compact_json_size_bytes({"x": ""})
        accepted = {"x": "a" * (limit - overhead)}
        self.assertEqual(executor.validate_seedance_request_size(accepted), limit)
        rejected = {"x": accepted["x"] + "a"}
        with self.assertRaises(Exception):
            executor.validate_seedance_request_size(rejected)

    def test_seedance_4k_estimation_rules(self):
        class Tasks:
            @staticmethod
            def list(**_kwargs):
                return SimpleNamespace(items=[])

        ark = SimpleNamespace(content_generation=SimpleNamespace(tasks=Tasks()))
        with_reference = asyncio.run(
            executor._get_api_estimated_time_async(
                ark,
                "dreamina-seedance-2-0-260128",
                5,
                "4k",
                content=[{"type": "video_url", "video_url": {"url": "x.mp4"}}],
            )
        )
        without_reference = asyncio.run(
            executor._get_api_estimated_time_async(
                ark, "dreamina-seedance-2-0-260128", 5, "4k", content=[]
            )
        )
        seedance25 = asyncio.run(
            executor._get_api_estimated_time_async(
                ark,
                "dreamina-seedance-2-5-260628",
                30,
                "720p",
                content=[{"type": "video_url", "video_url": {"url": "x.mp4"}}],
            )
        )
        self.assertEqual(with_reference[0], 5 * 90 + executor.DEFAULT_FALLBACK_BASE)
        self.assertEqual(without_reference[0], 5 * 45 + executor.DEFAULT_FALLBACK_BASE)
        self.assertEqual(seedance25[0], 30 * 40 + executor.DEFAULT_FALLBACK_BASE)

    def test_seedance25_task_type_error_is_localized(self):
        raw = (
            "The parameter(s) ratio and duration specified in the request are not valid. "
            "The task is determined as video editing. Issues: ratio must be adaptive; "
            "duration must be -1."
        )
        translated = nodes_video.format_api_error(Exception(raw))
        self.assertIn("adaptive", translated)
        self.assertIn("InvalidParameter.TaskTypeConstraint", translated)

    def test_mini_non_blocking_submission_returns_task_json_state(self):
        submitted = []

        class Tasks:
            @staticmethod
            def create(**kwargs):
                submitted.append(kwargs)
                return SimpleNamespace(id="task-mini-1")

            @staticmethod
            def list(**_kwargs):
                return SimpleNamespace(items=[])

        class ProgressServer:
            def send_progress_text(self, *_args, **_kwargs):
                return None

            def send_sync(self, *_args, **_kwargs):
                return None

        old_prompt_server = getattr(executor.PromptServer, "instance", None)
        executor.PromptServer.instance = ProgressServer()
        client = SimpleNamespace(
            ark=SimpleNamespace(
                content_generation=SimpleNamespace(tasks=Tasks())
            )
        )
        try:
            result = asyncio.run(
                executor.BytePlusGenerationExecutor(client, "test-node").run_batch_tasks(
                    model_name="dreamina-seedance-2-0-mini-260615",
                    content=[{"type": "text", "text": "test"}],
                    estimation_duration=5,
                    resolution="720p",
                    generation_count=1,
                    non_blocking=True,
                    non_blocking_cache_dict={},
                )
            )
        finally:
            if old_prompt_server is None:
                delattr(executor.PromptServer, "instance")
            else:
                executor.PromptServer.instance = old_prompt_server

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["task_ids"], ["task-mini-1"])
        self.assertEqual(submitted[0]["model"], "dreamina-seedance-2-0-mini-260615")
        self.assertNotIn("service_tier", submitted[0])

    def test_seedance25_submission_uses_seedance2_request_policy(self):
        submitted = []
        validated = []

        class Tasks:
            @staticmethod
            def create(**kwargs):
                submitted.append(kwargs)
                return SimpleNamespace(id="task-seedance25-1")

            @staticmethod
            def list(**_kwargs):
                return SimpleNamespace(items=[])

        class ProgressServer:
            def send_progress_text(self, *_args, **_kwargs):
                return None

            def send_sync(self, *_args, **_kwargs):
                return None

        old_prompt_server = getattr(executor.PromptServer, "instance", None)
        old_validate_request = executor.validate_seedance_request_size
        executor.PromptServer.instance = ProgressServer()
        executor.validate_seedance_request_size = lambda payload: validated.append(payload)
        client = SimpleNamespace(
            ark=SimpleNamespace(
                content_generation=SimpleNamespace(tasks=Tasks())
            )
        )
        try:
            result = asyncio.run(
                executor.BytePlusGenerationExecutor(client, "test-node").run_batch_tasks(
                    model_name="dreamina-seedance-2-5-260628",
                    content=[{"type": "text", "text": "test"}],
                    estimation_duration=30,
                    resolution="720p",
                    generation_count=1,
                    non_blocking=True,
                    non_blocking_cache_dict={},
                    service_tier="flex",
                    execution_expires_after=3600,
                )
            )
        finally:
            executor.validate_seedance_request_size = old_validate_request
            if old_prompt_server is None:
                delattr(executor.PromptServer, "instance")
            else:
                executor.PromptServer.instance = old_prompt_server

        self.assertEqual(result["task_ids"], ["task-seedance25-1"])
        self.assertEqual(submitted[0]["model"], "dreamina-seedance-2-5-260628")
        self.assertEqual(len(validated), 1)
        self.assertNotIn("service_tier", submitted[0])
        self.assertNotIn("execution_expires_after", submitted[0])


@requires_comfyui
class Seedream4PromptOptimizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_seedream_4_0_sends_prompt_optimization(self):
        import torch

        requests = []

        class Client:
            ark = SimpleNamespace()

            @staticmethod
            def check_quota(*_args):
                return None

            @staticmethod
            def update_usage(*_args):
                return None

        async def fake_stream(
            _self,
            _session,
            _ark_client,
            kwargs,
            idx,
            _enable_group_generation,
            _generation_count,
        ):
            requests.append(dict(kwargs))
            return torch.zeros((1, 2, 2, 3)), {"batch_index": idx}

        old_stream = executor.BytePlusGenerationExecutor.stream_generation_helper
        old_count = nodes_image.get_node_count_in_workflow
        old_hidden = getattr(nodes_image.BytePlusSeedream4, "hidden", None)
        old_prompt_server = getattr(executor.PromptServer, "instance", None)
        executor.BytePlusGenerationExecutor.stream_generation_helper = fake_stream
        nodes_image.get_node_count_in_workflow = lambda *_args, **_kwargs: 1
        executor.PromptServer.instance = SimpleNamespace()
        nodes_image.BytePlusSeedream4.hidden = SimpleNamespace(
            unique_id="test-node", prompt={}
        )

        common = {
            "client": Client(),
            "prompt": "product photo",
            "enable_group_generation": False,
            "max_images": 1,
            "size": "2K (adaptive)",
            "width": 2048,
            "height": 2048,
            "seed": 7,
            "generation_count": 1,
            "watermark": False,
            "prompt_optimization": "standard",
        }
        try:
            await nodes_image.BytePlusSeedream4.execute(
                model_version="seedream-4-0", **common
            )
            await nodes_image.BytePlusSeedream4.execute(
                model_version="seedream-4-0", **{**common, "prompt_optimization": "fast"}
            )
            await nodes_image.BytePlusSeedream4.execute(
                model_version="seedream-4-5", **common
            )
        finally:
            executor.BytePlusGenerationExecutor.stream_generation_helper = old_stream
            nodes_image.get_node_count_in_workflow = old_count
            if old_prompt_server is None:
                delattr(executor.PromptServer, "instance")
            else:
                executor.PromptServer.instance = old_prompt_server
            if old_hidden is None:
                delattr(nodes_image.BytePlusSeedream4, "hidden")
            else:
                nodes_image.BytePlusSeedream4.hidden = old_hidden

        self.assertEqual(requests[0]["optimize_prompt_options"].mode, "standard")
        self.assertEqual(requests[1]["optimize_prompt_options"].mode, "fast")
        self.assertNotIn("optimize_prompt_options", requests[2])


@requires_comfyui
class Seedream5ProTests(unittest.IsolatedAsyncioTestCase):
    def test_size_levels_and_custom_limits(self):
        resolve = nodes_image.resolve_seedream5_pro_size
        self.assertEqual(resolve("1.5K (adaptive)", 0, 0), "1.5K")
        self.assertEqual(resolve("2816x1584 (16:9)", 0, 0), "2816x1584")
        self.assertEqual(resolve("Custom", 1280, 720), "1280x720")
        self.assertEqual(resolve("Custom", 2150, 2150), "2150x2150")
        with self.assertRaises(Exception):
            resolve("Custom", 1279, 720)
        with self.assertRaises(Exception):
            resolve("Custom", 2160, 2160)
        with self.assertRaises(Exception):
            resolve("Custom", 4112, 256)

    async def test_pro_url_request_and_forbidden_fields(self):
        calls = []

        class Images:
            @staticmethod
            def generate(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    model=kwargs["model"],
                    created=1,
                    data=[SimpleNamespace(url="https://example.invalid/image.png")],
                )

        class Client:
            ark = SimpleNamespace(images=Images())

            @staticmethod
            def check_quota(*_args):
                return None

            @staticmethod
            def update_usage(*_args):
                return None

        async def fake_download(_session, _url):
            import torch

            return torch.zeros((1, 2, 2, 3))

        old_download = nodes_image.download_url_to_image_tensor_async
        old_count = nodes_image.get_node_count_in_workflow
        old_hidden = getattr(nodes_image.BytePlusSeedream5, "hidden", None)
        old_prompt_server = getattr(executor.PromptServer, "instance", None)
        nodes_image.download_url_to_image_tensor_async = fake_download
        nodes_image.get_node_count_in_workflow = lambda *_args, **_kwargs: 1
        executor.PromptServer.instance = SimpleNamespace()
        nodes_image.BytePlusSeedream5.hidden = SimpleNamespace(
            unique_id="test-node", prompt={}
        )
        try:
            await nodes_image.BytePlusSeedream5.execute(
                Client(),
                {
                    "model_version": "dola-seedream-5-0-pro",
                    "prompt": "product photo",
                    "size": "2K (Adaptive)",
                    "width": 2048,
                    "height": 2048,
                    "seed": 7,
                    "prompt_optimization": "fast",
                    "generation_count": 1,
                    "watermark": True,
                },
            )
        finally:
            nodes_image.download_url_to_image_tensor_async = old_download
            nodes_image.get_node_count_in_workflow = old_count
            if old_prompt_server is None:
                delattr(executor.PromptServer, "instance")
            else:
                executor.PromptServer.instance = old_prompt_server
            if old_hidden is None:
                delattr(nodes_image.BytePlusSeedream5, "hidden")
            else:
                nodes_image.BytePlusSeedream5.hidden = old_hidden

        self.assertEqual(len(calls), 1)
        request = calls[0]
        self.assertEqual(request["response_format"], "url")
        self.assertEqual(request["seed"], 7)
        self.assertTrue(request["watermark"])
        self.assertEqual(request["optimize_prompt_options"].mode, "fast")
        self.assertEqual(request["output_format"], "jpeg")
        self.assertNotIn("extra_body", request)
        self.assertNotIn("tools", request)
        self.assertNotIn("sequential_image_generation", request)

    async def _run_pro(self, model_config, images=None, reference_mask=None, rgba=False):
        import torch

        calls = []

        class Images:
            @staticmethod
            def generate(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    model=kwargs["model"],
                    created=1,
                    data=[SimpleNamespace(url="https://example.invalid/image.png")],
                )

        class Client:
            ark = SimpleNamespace(images=Images())

            @staticmethod
            def check_quota(*_args):
                return None

            @staticmethod
            def update_usage(*_args):
                return None

        async def fake_rgb(_session, _url):
            return torch.zeros((1, 2, 2, 3))

        async def fake_rgba(_session, _url):
            tensor = torch.ones((1, 2, 2, 4))
            tensor[..., 3] = 0.25
            return tensor

        old = (
            nodes_image.download_url_to_image_tensor_async,
            nodes_image.download_url_to_rgba_tensor_async,
            nodes_image.get_node_count_in_workflow,
            getattr(nodes_image.BytePlusSeedream5, "hidden", None),
            getattr(executor.PromptServer, "instance", None),
        )
        nodes_image.download_url_to_image_tensor_async = fake_rgb
        nodes_image.download_url_to_rgba_tensor_async = fake_rgba
        nodes_image.get_node_count_in_workflow = lambda *_args, **_kwargs: 1
        nodes_image.BytePlusSeedream5.hidden = SimpleNamespace(unique_id="test-node", prompt={})
        executor.PromptServer.instance = SimpleNamespace()
        try:
            result = await nodes_image.BytePlusSeedream5.execute(
                Client(),
                {"model_version": "dola-seedream-5-0-pro", "prompt": "edit", **model_config},
                images=images,
                reference_mask=reference_mask,
            )
        finally:
            nodes_image.download_url_to_image_tensor_async = old[0]
            nodes_image.download_url_to_rgba_tensor_async = old[1]
            nodes_image.get_node_count_in_workflow = old[2]
            if old[3] is None:
                delattr(nodes_image.BytePlusSeedream5, "hidden")
            else:
                nodes_image.BytePlusSeedream5.hidden = old[3]
            if old[4] is None:
                delattr(executor.PromptServer, "instance")
            else:
                executor.PromptServer.instance = old[4]
        return calls, result

    async def test_transparent_background_sends_png_with_alpha(self):
        import base64
        import io
        import PIL.Image
        import torch

        mask = torch.zeros((1, 8, 8))
        mask[:, :, :4] = 1.0  # left half transparent (Load Image convention)
        calls, result = await self._run_pro(
            {"background": "transparent", "output_format": "png"},
            images=torch.ones((1, 8, 8, 3)),
            reference_mask=mask,
        )
        request = calls[0]
        self.assertEqual(request["extra_body"], {"background": "transparent"})
        self.assertEqual(request["output_format"], "png")
        self.assertTrue(request["image"].startswith("data:image/png;base64,"))
        png = PIL.Image.open(io.BytesIO(base64.b64decode(request["image"].split(",", 1)[1])))
        self.assertEqual(png.mode, "RGBA")
        self.assertEqual(png.getpixel((0, 0))[3], 0)
        self.assertEqual(png.getpixel((7, 0))[3], 255)
        images, _response, output_mask = result
        self.assertEqual(images.shape[-1], 3)
        self.assertAlmostEqual(float(output_mask[0, 0, 0]), 0.75)

    async def test_transparent_background_validation(self):
        import torch

        with self.assertRaises(Exception):
            await self._run_pro({"background": "transparent", "output_format": "png"})
        with self.assertRaises(Exception):
            await self._run_pro(
                {"background": "transparent", "output_format": "jpeg"},
                images=torch.ones((1, 8, 8, 3)),
            )

    async def test_opaque_output_has_empty_mask(self):
        _calls, result = await self._run_pro({})
        images, _response, output_mask = result
        self.assertEqual(tuple(output_mask.shape), tuple(images.shape[:3]))
        self.assertEqual(float(output_mask.sum()), 0.0)

    async def test_pro_reference_limit_is_ten(self):
        import torch

        fake_client = SimpleNamespace(ark=SimpleNamespace())
        old_hidden = getattr(nodes_image.BytePlusSeedream5, "hidden", None)
        nodes_image.BytePlusSeedream5.hidden = SimpleNamespace(
            unique_id="test-node", prompt={}
        )
        try:
            with self.assertRaises(Exception):
                await nodes_image.BytePlusSeedream5.execute(
                    fake_client,
                    {
                        "model_version": "dola-seedream-5-0-pro",
                        "prompt": "edit",
                    },
                    images=torch.zeros((11, 8, 8, 3)),
                )
        finally:
            if old_hidden is None:
                delattr(nodes_image.BytePlusSeedream5, "hidden")
            else:
                nodes_image.BytePlusSeedream5.hidden = old_hidden


@requires_comfyui
class Seedance25ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_seedance25_audio_only_uses_official_model(self):
        captured = {}

        async def fake_common(_self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return "ok"

        def fake_append_audio(_self, content, _audio, role, max_duration):
            content.append(
                {
                    "type": "audio_url",
                    "audio_url": {"url": "data:audio/wav;base64,AA=="},
                    "role": role,
                }
            )
            self.assertEqual(max_duration, 30.2)
            return 5.0, 4

        old_common = nodes_video.BytePlusVideoBase._common_generation_logic
        old_append_audio = nodes_video.BytePlusVideoBase._append_audio_content
        old_hidden = getattr(nodes_video.BytePlusSeedance2, "hidden", None)
        nodes_video.BytePlusVideoBase._common_generation_logic = fake_common
        nodes_video.BytePlusVideoBase._append_audio_content = fake_append_audio
        nodes_video.BytePlusSeedance2.hidden = SimpleNamespace(
            unique_id="seedance25-test", prompt={}
        )
        try:
            result = await nodes_video.BytePlusSeedance2.execute(
                SimpleNamespace(),
                {
                    "model_version": "dreamina-seedance-2-5",
                    "prompt": "",
                    "duration": 30,
                    "auto_duration": False,
                    "resolution": "720p",
                    "aspect_ratio": "adaptive",
                },
                ref_audios=[object()],
            )
        finally:
            nodes_video.BytePlusVideoBase._common_generation_logic = old_common
            nodes_video.BytePlusVideoBase._append_audio_content = old_append_audio
            if old_hidden is None:
                delattr(nodes_video.BytePlusSeedance2, "hidden")
            else:
                nodes_video.BytePlusSeedance2.hidden = old_hidden

        self.assertEqual(result, "ok")
        self.assertEqual(captured["args"][1], "")
        self.assertEqual(captured["args"][2], 30)
        self.assertEqual(
            captured["kwargs"]["model_name"], "dreamina-seedance-2-5-260628"
        )
        self.assertEqual(captured["kwargs"]["extra_api_params"]["generate_audio"], True)

    async def test_seedance25_video_editing_requires_adaptive_auto_duration(self):
        old_hidden = getattr(nodes_video.BytePlusSeedance2, "hidden", None)
        nodes_video.BytePlusSeedance2.hidden = SimpleNamespace(
            unique_id="seedance25-test", prompt={}
        )
        try:
            with self.assertRaises(Exception):
                await nodes_video.BytePlusSeedance2.execute(
                    SimpleNamespace(),
                    {
                        "model_version": "dreamina-seedance-2-5",
                        "prompt": "edit",
                        "duration": 5,
                        "auto_duration": False,
                        "resolution": "720p",
                        "aspect_ratio": "16:9",
                    },
                    ref_videos=[object()],
                )
        finally:
            if old_hidden is None:
                delattr(nodes_video.BytePlusSeedance2, "hidden")
            else:
                nodes_video.BytePlusSeedance2.hidden = old_hidden


@requires_comfyui
class Seedance25TaskTypeTests(unittest.IsolatedAsyncioTestCase):
    def test_task_type_validation(self):
        validate = nodes_video.validate_seedance25_task_type
        validate("reference", True, "16:9", False)
        validate("extend", True, "adaptive", False)
        validate("edit", True, "adaptive", True)
        validate("auto", False, "16:9", False)
        for args in (
            ("edit", False, "adaptive", True),
            ("extend", False, "adaptive", True),
            ("edit", True, "adaptive", False),
            ("edit", True, "16:9", True),
            ("extend", True, "16:9", True),
            ("auto", True, "16:9", True),
        ):
            with self.subTest(args=args), self.assertRaises(Exception):
                validate(*args)

    async def _run_seedance25(self, model_config, **inputs):
        captured = {}

        async def fake_common(_self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return "ok"

        old_common = nodes_video.BytePlusVideoBase._common_generation_logic
        old_hidden = getattr(nodes_video.BytePlusSeedance2, "hidden", None)
        nodes_video.BytePlusVideoBase._common_generation_logic = fake_common
        nodes_video.BytePlusSeedance2.hidden = SimpleNamespace(
            unique_id="seedance25-test", prompt={}
        )
        try:
            await nodes_video.BytePlusSeedance2.execute(
                SimpleNamespace(),
                {
                    "model_version": "dreamina-seedance-2-5",
                    "prompt": "continue the shot",
                    "duration": 10,
                    "auto_duration": False,
                    "resolution": "1080p",
                    **model_config,
                },
                **inputs,
            )
        finally:
            nodes_video.BytePlusVideoBase._common_generation_logic = old_common
            if old_hidden is None:
                delattr(nodes_video.BytePlusSeedance2, "hidden")
            else:
                nodes_video.BytePlusSeedance2.hidden = old_hidden
        return captured

    async def test_reference_video_url_skips_upload_and_sets_task_type(self):
        captured = await self._run_seedance25(
            {"aspect_ratio": "16:9", "task_type": "reference", "output_format": "mov"},
            ref_video_urls="https://example.invalid/clip.mp4\nasset://asset-123\n",
        )
        content = captured["kwargs"]["content"]
        urls = [item["video_url"]["url"] for item in content if item["type"] == "video_url"]
        self.assertEqual(urls, ["https://example.invalid/clip.mp4", "asset://asset-123"])
        extra = captured["kwargs"]["extra_api_params"]
        self.assertEqual(extra["omni_reference_task_type"], "reference")
        self.assertEqual(extra["output_format"], "mov")
        self.assertEqual(captured["args"][3], "1080p")
        self.assertEqual(captured["args"][4], "16:9")

    async def test_default_task_type_and_format_are_not_sent(self):
        captured = await self._run_seedance25({"aspect_ratio": "16:9"})
        extra = captured["kwargs"]["extra_api_params"]
        self.assertNotIn("omni_reference_task_type", extra)
        self.assertNotIn("output_format", extra)

    async def test_first_frame_requires_adaptive_ratio(self):
        import torch

        with self.assertRaises(Exception) as ctx:
            await self._run_seedance25(
                {"aspect_ratio": "16:9"},
                first_frame_image=torch.zeros((1, 720, 1280, 3)),
            )
        self.assertIn("adaptive", str(ctx.exception))

    async def test_reference_video_url_must_be_mp4_or_mov(self):
        with self.assertRaises(Exception):
            await self._run_seedance25(
                {"aspect_ratio": "16:9", "task_type": "reference"},
                ref_video_urls="https://example.invalid/clip.webm",
            )


@requires_comfyui
class SeedanceDraftModeTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, model_config, node_id="draft-test"):
        captured = {}

        async def fake_common(_self, *args, **kwargs):
            captured["common"] = (args, kwargs)
            return "draft"

        async def fake_prebuilt(_self, *args, **kwargs):
            captured["prebuilt"] = (args, kwargs)
            return "final"

        old_common = nodes_video.BytePlusVideoBase._common_generation_logic
        old_prebuilt = nodes_video.BytePlusVideoBase._run_prebuilt_content
        old_hidden = getattr(nodes_video.BytePlusSeedance2, "hidden", None)
        nodes_video.BytePlusVideoBase._common_generation_logic = fake_common
        nodes_video.BytePlusVideoBase._run_prebuilt_content = fake_prebuilt
        nodes_video.BytePlusSeedance2.hidden = SimpleNamespace(unique_id=node_id, prompt={})
        try:
            result = await nodes_video.BytePlusSeedance2.execute(
                SimpleNamespace(),
                {
                    "model_version": "dreamina-seedance-2-5",
                    "prompt": "a fox in the snow",
                    "duration": 5,
                    "auto_duration": False,
                    "aspect_ratio": "16:9",
                    **model_config,
                },
            )
        finally:
            nodes_video.BytePlusVideoBase._common_generation_logic = old_common
            nodes_video.BytePlusVideoBase._run_prebuilt_content = old_prebuilt
            if old_hidden is None:
                delattr(nodes_video.BytePlusSeedance2, "hidden")
            else:
                nodes_video.BytePlusSeedance2.hidden = old_hidden
        return result, captured

    async def test_draft_mode_forces_480p(self):
        result, captured = await self._run({"draft_mode": True, "resolution": "1080p"})
        self.assertEqual(result, "draft")
        args, kwargs = captured["common"]
        self.assertEqual(args[3], "480p")
        self.assertTrue(kwargs["extra_api_params"]["draft"])
        self.assertFalse(kwargs["return_last_frame"])

    async def test_final_from_draft_task_id_sends_only_draft_reference(self):
        result, captured = await self._run(
            {"draft_task_id": "cgt-draft-1", "resolution": "1080p", "output_format": "mov"}
        )
        self.assertEqual(result, "final")
        args, kwargs = captured["prebuilt"]
        self.assertEqual(args[2], "dreamina-seedance-2-5-260628")
        self.assertEqual(args[3], [{"type": "draft_task", "draft_task": {"id": "cgt-draft-1"}}])
        self.assertEqual(kwargs["extra_api_params"], {"resolution": "1080p", "output_format": "mov"})

    async def test_final_resolution_limits(self):
        with self.assertRaises(Exception):
            await self._run({"draft_task_id": "cgt-draft-1", "resolution": "720p"})
        _result, captured = await self._run(
            {
                "model_version": "dreamina-seedance-2-5-premium",
                "draft_task_id": "cgt-draft-1",
                "resolution": "4k",
            }
        )
        args, kwargs = captured["prebuilt"]
        self.assertEqual(args[2], "dreamina-seedance-2-5-premium-260915")
        self.assertEqual(kwargs["extra_api_params"]["resolution"], "4k")

    async def test_reuse_last_draft(self):
        _result, captured = await self._run(
            {"draft_mode": True, "resolution": "480p"}, node_id="reuse-node"
        )
        captured["common"][1]["on_tasks_created"]([SimpleNamespace(id="cgt-d1"), SimpleNamespace(id="cgt-d2")])
        result, captured = await self._run(
            {"draft_mode": True, "reuse_last_draft_task": True, "resolution": "1080p"},
            node_id="reuse-node",
        )
        self.assertEqual(result, "final")
        args, kwargs = captured["prebuilt"]
        self.assertEqual(
            args[3],
            [
                [{"type": "draft_task", "draft_task": {"id": "cgt-d1"}}],
                [{"type": "draft_task", "draft_task": {"id": "cgt-d2"}}],
            ],
        )
        self.assertEqual(kwargs["generation_count"], 2)


@requires_comfyui
class SeedreamLayerDecompositionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _png_b64(size, color):
        import base64
        import io
        import PIL.Image

        buffer = io.BytesIO()
        PIL.Image.new("RGBA", size, color).save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    async def _run(self, save_layers=False, output_dir=None):
        import torch

        calls = []
        base = SimpleNamespace(b64_json=self._png_b64((8, 8), (255, 0, 0, 255)), z_index=0,
                               bounding_box=None, name=None, description=None, size="8x8")
        layer = SimpleNamespace(
            b64_json=self._png_b64((4, 4), (0, 255, 0, 128)),
            z_index=1,
            bounding_box=SimpleNamespace(absolute=[2, 2, 6, 6], normalized=[250, 250, 750, 750]),
            name="leaf",
            description="a green leaf",
            size="4x4",
        )

        class Images:
            @staticmethod
            def generate(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(model=kwargs["model"], data=[layer, base])

        client = SimpleNamespace(
            ark=SimpleNamespace(images=Images()),
            check_quota=lambda *_args: None,
            update_usage=lambda *_args: None,
        )
        old_output = nodes_image.folder_paths.get_output_directory
        if output_dir:
            nodes_image.folder_paths.get_output_directory = lambda: output_dir
        try:
            result = await nodes_image.BytePlusSeedreamLayers.execute(
                client,
                "dola-seedream-5-0-pro",
                torch.ones((1, 600, 600, 3)),
                save_layers=save_layers,
            )
        finally:
            nodes_image.folder_paths.get_output_directory = old_output
        return calls, result

    async def test_request_and_outputs(self):
        calls, result = await self._run()
        request = calls[0]
        self.assertTrue(request["layer_decomposition"])
        self.assertEqual(request["size"], "auto")
        self.assertEqual(request["response_format"], "b64_json")
        self.assertTrue(request["image"].startswith("data:image/png;base64,"))

        base_image, layers, layer_masks, layers_json = result
        self.assertEqual(tuple(base_image.shape), (1, 8, 8, 3))
        self.assertEqual(tuple(layers.shape), (1, 8, 8, 3))
        self.assertEqual(tuple(layer_masks.shape), (1, 8, 8))
        self.assertEqual(float(layer_masks[0, 0, 0]), 1.0)  # outside the layer: transparent
        self.assertAlmostEqual(float(layer_masks[0, 3, 3]), 1.0 - 128 / 255, places=2)
        self.assertAlmostEqual(float(layers[0, 3, 3, 1]), 1.0, places=2)
        info = json.loads(layers_json)["layers"]
        self.assertEqual([item["z_index"] for item in info], [0, 1])
        self.assertEqual(info[1]["name"], "leaf")
        self.assertEqual(info[1]["bounding_box"]["absolute"], [2, 2, 6, 6])

    async def test_saves_original_layer_pngs(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            _calls, result = await self._run(save_layers=True, output_dir=tmp)
            info = json.loads(result[3])["layers"]
            for item in info:
                self.assertTrue(os.path.exists(os.path.join(tmp, item["file"])))
            self.assertTrue(info[1]["file"].endswith("_layer01.png"))

    async def test_input_pixel_limit(self):
        import torch

        with self.assertRaises(Exception):
            await nodes_image.BytePlusSeedreamLayers.execute(
                SimpleNamespace(check_quota=lambda *_a: None),
                "dola-seedream-5-0-pro",
                torch.ones((1, 100, 100, 3)),
            )


@requires_comfyui
class ComfyStorageUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_api_nodes_explains_alternatives(self):
        old_module = sys.modules.get("comfy_api_nodes.util", ...)
        sys.modules["comfy_api_nodes.util"] = None
        try:
            with self.assertRaises(Exception) as ctx:
                await nodes_video.upload_video_to_comfy_storage(object, object())
        finally:
            if old_module is ...:
                sys.modules.pop("comfy_api_nodes.util", None)
            else:
                sys.modules["comfy_api_nodes.util"] = old_module
        self.assertIn("ref_video_urls", str(ctx.exception))
        self.assertIn("--disable-api-nodes", str(ctx.exception))

    async def test_upload_failure_mentions_comfy_login(self):
        async def failing_upload(*_args, **_kwargs):
            raise RuntimeError("401 Unauthorized")

        old_module = sys.modules.get("comfy_api_nodes.util", ...)
        sys.modules["comfy_api_nodes.util"] = SimpleNamespace(
            upload_video_to_comfyapi=failing_upload
        )
        try:
            with self.assertRaises(Exception) as ctx:
                await nodes_video.upload_video_to_comfy_storage(object, object())
        finally:
            if old_module is ...:
                sys.modules.pop("comfy_api_nodes.util", None)
            else:
                sys.modules["comfy_api_nodes.util"] = old_module
        message = str(ctx.exception)
        self.assertIn("401 Unauthorized", message)
        self.assertIn("Comfy.org", message)
        self.assertIn("ref_video_urls", message)


@requires_comfyui
class ReferenceVideoTests(unittest.TestCase):
    class FakeVideo:
        def __init__(self, fps=24, video_codec="h264", audio_codec="aac"):
            self.fps = fps
            self.video_codec = video_codec
            self.audio_codec = audio_codec

        def get_container_format(self):
            return "mp4"

        def get_dimensions(self):
            return 1280, 720

        def get_stream_source(self):
            return io.BytesIO(b"video")

        def get_duration(self):
            return 5

        def get_fps(self):
            return self.fps

        def get_video_codec(self):
            return self.video_codec

        def get_audio_codec(self):
            return self.audio_codec

    def test_media_limits_and_known_metadata(self):
        helper = nodes_video.BytePlusVideoBase()
        self.assertEqual(helper._validate_single_reference_video(self.FakeVideo()), 5)
        with self.assertRaises(Exception):
            helper._validate_single_reference_video(self.FakeVideo(fps=23.9))
        with self.assertRaises(Exception):
            helper._validate_single_reference_video(
                self.FakeVideo(video_codec="vp9")
            )
        with self.assertRaises(Exception):
            helper._validate_single_reference_video(
                self.FakeVideo(audio_codec="opus")
            )

    def test_unknown_codec_metadata_is_not_rejected(self):
        helper = nodes_video.BytePlusVideoBase()
        self.assertEqual(
            helper._validate_single_reference_video(
                self.FakeVideo(video_codec="", audio_codec="")
            ),
            5,
        )

    def test_seedance25_reference_video_duration_limit(self):
        helper = nodes_video.BytePlusVideoBase()
        long_video = self.FakeVideo()
        long_video.get_duration = lambda: 25
        with self.assertRaises(Exception):
            helper._validate_single_reference_video(long_video)
        self.assertEqual(
            helper._validate_single_reference_video(long_video, max_duration=30.2),
            25,
        )


if __name__ == "__main__":
    unittest.main()
