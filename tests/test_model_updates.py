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

    # ComfyUI owns the top-level ``utils`` package. Preload it from ComfyUI so a
    # ``utils`` module elsewhere on sys.path cannot shadow it.
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


def assert_matches_sdk(method, kwargs):
    """
    Bind request kwargs to the real BytePlus SDK method signature, so a wrong
    parameter name (e.g. expire_at vs expires_at) fails here and not only
    against the live API. Fakes alone accept anything.
    """
    import inspect

    fn = inspect.unwrap(method)
    for cell in fn.__closure__ or ():
        if inspect.isfunction(cell.cell_contents) and cell.cell_contents.__name__ == fn.__name__:
            fn = cell.cell_contents
    inspect.signature(fn).bind(None, **kwargs)


@requires_comfyui
class ModelConfigurationTests(unittest.TestCase):
    def test_byteplus_model_ids_and_defaults(self):
        self.assertEqual(
            models_config.SEEDREAM_5_MODEL_MAP["dola-seedream-5-0-pro"],
            "dola-seedream-5-0-pro-260628",
        )
        self.assertEqual(
            models_config.SEEDREAM_5_MODEL_MAP["dola-seedream-5-0-flash"],
            "dola-seedream-5-0-flash-260915",
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

        flash = next(
            option
            for option in image_combo.options
            if option.key == "dola-seedream-5-0-flash"
        )
        self.assertEqual(
            [item.id for item in flash.inputs],
            [item.id for item in pro.inputs if item.id != "prompt_optimization"],
        )
        layer_model = next(
            item for item in nodes_image.BytePlusSeedreamLayers.define_schema().inputs
            if item.id == "model"
        )
        self.assertIn("dola-seedream-5-0-flash", layer_model.options)

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
            models_config.VIDEO_2_MODEL_RESOLUTIONS["dreamina-seedance-2-5-premium"],
            ["4k"],
        )
        premium_option = next(
            option
            for option in nodes_video.BytePlusSeedance2.define_schema().inputs[1].options
            if option.key == "dreamina-seedance-2-5-premium"
        )
        resolution_input = next(item for item in premium_option.inputs if item.id == "resolution")
        self.assertEqual(resolution_input.options, ["4k"])
        self.assertEqual(resolution_input.default, "4k")
        self.assertEqual(
            nodes_video.validate_seedance2_resolution("dreamina-seedance-2-5-premium", "4k"),
            "4k",
        )
        for resolution in ("480p", "720p", "1080p"):
            with self.subTest(resolution=resolution):
                with self.assertRaises(Exception):
                    nodes_video.validate_seedance2_resolution(
                        "dreamina-seedance-2-5-premium", resolution
                    )
        self.assertEqual(
            nodes_video.validate_seedance2_resolution(
                "dreamina-seedance-2-5-premium", "480p", draft_mode=True
            ),
            "480p",
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
class Seedream5UrlModelTests(unittest.IsolatedAsyncioTestCase):
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

    async def _run_url_model(
        self,
        model_config,
        images=None,
        reference_mask=None,
        rgba=False,
        model_version="dola-seedream-5-0-pro",
    ):
        import torch
        from byteplussdkarkruntime.resources.images.images import Images as SdkImages

        calls = []

        class Images:
            @staticmethod
            def generate(**kwargs):
                assert_matches_sdk(SdkImages.generate, kwargs)
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
                {"model_version": model_version, "prompt": "edit", **model_config},
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
        calls, result = await self._run_url_model(
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
            await self._run_url_model({"background": "transparent", "output_format": "png"})
        with self.assertRaises(Exception):
            await self._run_url_model(
                {"background": "transparent", "output_format": "jpeg"},
                images=torch.ones((1, 8, 8, 3)),
            )

    async def test_opaque_output_has_empty_mask(self):
        _calls, result = await self._run_url_model({})
        images, _response, output_mask = result
        self.assertEqual(tuple(output_mask.shape), tuple(images.shape[:3]))
        self.assertEqual(float(output_mask.sum()), 0.0)

    async def test_pro_and_flash_reference_limit_is_ten(self):
        import torch

        for model_version in ("dola-seedream-5-0-pro", "dola-seedream-5-0-flash"):
            with self.subTest(model_version=model_version):
                calls, _result = await self._run_url_model(
                    {}, images=torch.zeros((10, 8, 8, 3)), model_version=model_version
                )
                self.assertEqual(len(calls[0]["image"]), 10)
                with self.assertRaises(Exception) as ctx:
                    await self._run_url_model(
                        {}, images=torch.zeros((11, 8, 8, 3)), model_version=model_version
                    )
                self.assertIn("cannot exceed 10", str(ctx.exception))

    async def test_transparent_without_mask_sends_opaque_alpha(self):
        import base64
        import io
        import PIL.Image
        import torch

        calls, _result = await self._run_url_model(
            {"background": "transparent", "output_format": "png"},
            images=torch.ones((1, 8, 8, 3)),
        )
        png = PIL.Image.open(
            io.BytesIO(base64.b64decode(calls[0]["image"].split(",", 1)[1]))
        )
        self.assertEqual(png.mode, "RGBA")
        self.assertEqual(png.getpixel((0, 0))[3], 255)

    async def test_flash_uses_url_request_without_fast_optimization(self):
        calls, _result = await self._run_url_model(
            {"size": "2K (adaptive)", "seed": 7, "watermark": True},
            model_version="dola-seedream-5-0-flash",
        )
        request = calls[0]
        self.assertEqual(request["model"], "dola-seedream-5-0-flash-260915")
        self.assertEqual(request["size"], "2K")
        self.assertEqual(request["response_format"], "url")
        self.assertEqual(request["seed"], 7)
        self.assertTrue(request["watermark"])
        self.assertNotIn("optimize_prompt_options", request)
        self.assertNotIn("sequential_image_generation", request)

    async def test_flash_transparent_background_returns_mask(self):
        import torch

        calls, result = await self._run_url_model(
            {"background": "transparent", "output_format": "png"},
            images=torch.ones((1, 8, 8, 3)),
            reference_mask=torch.ones((1, 8, 8)),
            model_version="dola-seedream-5-0-flash",
        )
        self.assertEqual(calls[0]["extra_body"], {"background": "transparent"})
        self.assertEqual(calls[0]["output_format"], "png")
        self.assertAlmostEqual(float(result[2][0, 0, 0]), 0.75)

    async def test_flash_rejects_stale_fast_setting(self):
        with self.assertRaisesRegex(Exception, "Flash supports only standard"):
            await self._run_url_model(
                {"prompt_optimization": "fast"},
                model_version="dola-seedream-5-0-flash",
            )


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
            with self.assertRaises(Exception) as ctx:
                await nodes_video.BytePlusSeedance2.execute(
                    SimpleNamespace(),
                    {
                        "model_version": "dreamina-seedance-2-5",
                        "prompt": "edit",
                        "task_type": "edit",
                        "duration": 5,
                        "auto_duration": False,
                        "resolution": "720p",
                        "aspect_ratio": "16:9",
                    },
                    ref_video_urls="https://example.invalid/clip.mp4",
                )
        finally:
            if old_hidden is None:
                delattr(nodes_video.BytePlusSeedance2, "hidden")
            else:
                nodes_video.BytePlusSeedance2.hidden = old_hidden
        self.assertIn("task_type edit", str(ctx.exception))


@requires_comfyui
class Seedance25TaskTypeTests(unittest.IsolatedAsyncioTestCase):
    def test_task_type_validation(self):
        validate = nodes_video.validate_seedance25_task_type
        validate("reference", True, "16:9", False)
        validate("extend", True, "adaptive", False)
        validate("edit", True, "adaptive", True)
        validate("auto", False, "16:9", False)
        validate("auto", True, "16:9", False)
        for args in (
            ("edit", False, "adaptive", True),
            ("extend", False, "adaptive", True),
            ("edit", True, "adaptive", False),
            ("edit", True, "16:9", True),
            ("extend", True, "16:9", True),
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

    async def test_auto_task_type_accepts_reference_video_with_fixed_ratio(self):
        captured = await self._run_seedance25(
            {"aspect_ratio": "16:9"},
            ref_video_urls="https://example.invalid/clip.mp4",
        )
        self.assertEqual(captured["args"][4], "16:9")
        self.assertNotIn("omni_reference_task_type", captured["kwargs"]["extra_api_params"])

    async def test_first_frame_conflicts_with_reference_video_urls(self):
        import torch

        with self.assertRaises(Exception) as ctx:
            await self._run_seedance25(
                {"aspect_ratio": "adaptive"},
                first_frame_image=torch.zeros((1, 720, 1280, 3)),
                ref_video_urls="https://example.invalid/clip.mp4",
            )
        self.assertIn("First/last frame mode cannot be used together", str(ctx.exception))

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

        result, captured = await self._run(
            {
                "model_version": "dreamina-seedance-2-5-premium",
                "draft_mode": True,
                "resolution": "4k",
            }
        )
        self.assertEqual(result, "draft")
        args, kwargs = captured["common"]
        self.assertEqual(args[3], "480p")
        self.assertTrue(kwargs["extra_api_params"]["draft"])

    async def test_premium_normal_generation_requires_4k(self):
        result, captured = await self._run(
            {"model_version": "dreamina-seedance-2-5-premium", "resolution": "4k"}
        )
        self.assertEqual(result, "draft")
        args, kwargs = captured["common"]
        self.assertEqual(args[3], "4k")
        self.assertNotIn("draft", kwargs["extra_api_params"])

        for resolution in ("480p", "720p", "1080p"):
            with self.subTest(resolution=resolution):
                with self.assertRaises(Exception):
                    await self._run(
                        {
                            "model_version": "dreamina-seedance-2-5-premium",
                            "resolution": resolution,
                        }
                    )

    async def test_final_from_draft_task_id_sends_only_draft_reference(self):
        result, captured = await self._run(
            {
                "draft_mode": True,
                "draft_task_id": "cgt-draft-1",
                "resolution": "1080p",
                "output_format": "mov",
            }
        )
        self.assertEqual(result, "final")
        args, kwargs = captured["prebuilt"]
        self.assertEqual(args[2], "dreamina-seedance-2-5-260628")
        self.assertEqual(args[3], [{"type": "draft_task", "draft_task": {"id": "cgt-draft-1"}}])
        self.assertEqual(kwargs["extra_api_params"], {"resolution": "1080p", "output_format": "mov"})

    async def test_final_resolution_limits(self):
        with self.assertRaises(Exception) as ctx:
            await self._run(
                {"draft_mode": True, "draft_task_id": "cgt-draft-1", "resolution": "720p"}
            )
        self.assertIn("support only 1080p", str(ctx.exception))
        _result, captured = await self._run(
            {
                "model_version": "dreamina-seedance-2-5-premium",
                "draft_mode": True,
                "draft_task_id": "cgt-draft-1",
                "resolution": "4k",
            }
        )
        args, kwargs = captured["prebuilt"]
        self.assertEqual(args[2], "dreamina-seedance-2-5-premium-260915")
        self.assertEqual(kwargs["extra_api_params"]["resolution"], "4k")
        # Premium finals are 4K only (the live API rejects 1080p).
        with self.assertRaises(Exception) as ctx:
            await self._run(
                {
                    "model_version": "dreamina-seedance-2-5-premium",
                    "draft_mode": True,
                    "draft_task_id": "cgt-draft-1",
                    "resolution": "1080p",
                }
            )
        self.assertIn("support only 4k", str(ctx.exception))

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

    async def test_hidden_draft_task_id_is_ignored(self):
        # Draft mode off: draft_task_id is hidden in the UI and must not be used.
        result, captured = await self._run(
            {"draft_mode": False, "draft_task_id": "cgt-old", "resolution": "720p"}
        )
        self.assertEqual(result, "draft")
        self.assertNotIn("prebuilt", captured)
        self.assertNotIn("draft", captured["common"][1]["extra_api_params"])
        # Reuse on: the remembered draft wins over a hidden leftover ID.
        nodes_video.LAST_SEEDANCE_2_DRAFT_TASKS["hidden-node"] = {
            "model": "dreamina-seedance-2-5", "ids": ["cgt-new"],
        }
        _result, captured = await self._run(
            {
                "draft_mode": True,
                "reuse_last_draft_task": True,
                "draft_task_id": "cgt-old",
                "resolution": "1080p",
            },
            node_id="hidden-node",
        )
        self.assertEqual(
            captured["prebuilt"][0][3], [{"type": "draft_task", "draft_task": {"id": "cgt-new"}}]
        )

    async def test_reuse_without_remembered_draft_raises(self):
        nodes_video.LAST_SEEDANCE_2_DRAFT_TASKS.pop("empty-node", None)
        with self.assertRaises(Exception) as ctx:
            await self._run(
                {"draft_mode": True, "reuse_last_draft_task": True, "resolution": "1080p"},
                node_id="empty-node",
            )
        self.assertIn("no draft to reuse", str(ctx.exception))
        # A draft remembered for another model is not reused either.
        nodes_video.LAST_SEEDANCE_2_DRAFT_TASKS["empty-node"] = {
            "model": "dreamina-seedance-2-5-premium", "ids": ["cgt-premium"],
        }
        with self.assertRaises(Exception):
            await self._run(
                {"draft_mode": True, "reuse_last_draft_task": True, "resolution": "1080p"},
                node_id="empty-node",
            )


@requires_comfyui
class SeedanceDraftRequestTests(unittest.IsolatedAsyncioTestCase):
    """End to end through the real executor: assert the request that is sent."""

    async def _submit(self, node_cls, model_config=None, **inputs):
        submitted = []
        quota_checks = []

        from byteplussdkarkruntime.resources.content_generation.tasks import Tasks as SdkTasks

        class Tasks:
            @staticmethod
            def create(**kwargs):
                assert_matches_sdk(SdkTasks.create, kwargs)
                submitted.append(kwargs)
                return SimpleNamespace(id=f"cgt-{len(submitted)}")

            @staticmethod
            def list(**kwargs):
                assert_matches_sdk(SdkTasks.list, kwargs)
                return SimpleNamespace(items=[])

        client = SimpleNamespace(
            ark=SimpleNamespace(content_generation=SimpleNamespace(tasks=Tasks())),
            check_quota=lambda model, cost: quota_checks.append((model, cost)),
            update_usage=lambda *_args: None,
        )
        old_server = getattr(executor.PromptServer, "instance", None)
        old_hidden = getattr(node_cls, "hidden", None)
        executor.PromptServer.instance = SimpleNamespace(
            send_progress_text=lambda *_a, **_k: None, send_sync=lambda *_a, **_k: None
        )
        node_cls.hidden = SimpleNamespace(unique_id=f"req-{id(inputs)}", prompt={})
        try:
            if model_config is not None:
                await node_cls.execute(client, model_config, **inputs)
            else:
                await node_cls.execute(client, **inputs)
        finally:
            if old_server is None:
                delattr(executor.PromptServer, "instance")
            else:
                executor.PromptServer.instance = old_server
            if old_hidden is None:
                delattr(node_cls, "hidden")
            else:
                node_cls.hidden = old_hidden
        return submitted, quota_checks

    SEEDANCE25 = {
        "model_version": "dreamina-seedance-2-5",
        "prompt": "a fox in the snow",
        "duration": 5,
        "auto_duration": False,
        "aspect_ratio": "16:9",
        "non_blocking": True,
    }

    async def test_draft_request(self):
        submitted, quota_checks = await self._submit(
            nodes_video.BytePlusSeedance2,
            {**self.SEEDANCE25, "draft_mode": True, "resolution": "1080p"},
        )
        request = submitted[0]
        self.assertTrue(request["draft"])
        self.assertEqual(request["resolution"], "480p")
        self.assertFalse(request["return_last_frame"])
        self.assertEqual(request["content"][0], {"type": "text", "text": "a fox in the snow"})
        self.assertGreater(quota_checks[0][1], 0)

    async def test_final_from_draft_request(self):
        submitted, quota_checks = await self._submit(
            nodes_video.BytePlusSeedance2,
            {
                **self.SEEDANCE25,
                "draft_mode": True,
                "draft_task_id": "cgt-draft-1",
                "resolution": "1080p",
                "output_format": "mov",
            },
        )
        request = submitted[0]
        self.assertEqual(request["model"], "dreamina-seedance-2-5-260628")
        self.assertEqual(
            request["content"], [{"type": "draft_task", "draft_task": {"id": "cgt-draft-1"}}]
        )
        self.assertEqual(request["resolution"], "1080p")
        self.assertEqual(request["output_format"], "mov")
        for reused in (
            "ratio", "duration", "frames", "seed", "generate_audio",
            "omni_reference_task_type", "draft", "service_tier",
            "execution_expires_after",
        ):
            self.assertNotIn(reused, request)
        # Final renders are checked against the quota too.
        self.assertEqual(quota_checks[0][0], "dreamina-seedance-2-5-260628")
        self.assertGreater(quota_checks[0][1], 0)

    async def test_seedance15_ignores_hidden_draft_task_id(self):
        submitted, _quota = await self._submit(
            nodes_video.BytePlusSeedance1_5,
            model_version="seedance-1-5-pro",
            prompt="a fox",
            generate_audio=True,
            auto_duration=False,
            duration=5,
            resolution="720p",
            aspect_ratio="16:9",
            camerafixed=False,
            enable_random_seed=False,
            seed=1,
            generation_count=1,
            filename_prefix="test",
            save_last_frame_batch=False,
            enable_offline_inference=False,
            non_blocking=True,
            draft_mode=False,
            reuse_last_draft_task=False,
            draft_task_id="cgt-old",
        )
        self.assertEqual(submitted[0]["content"][0], {"type": "text", "text": "a fox"})

    async def test_seedance15_reuse_without_draft_raises(self):
        with self.assertRaises(Exception) as ctx:
            await self._submit(
                nodes_video.BytePlusSeedance1_5,
                model_version="seedance-1-5-pro",
                prompt="a fox",
                generate_audio=True,
                auto_duration=False,
                duration=5,
                resolution="720p",
                aspect_ratio="16:9",
                camerafixed=False,
                enable_random_seed=False,
                seed=1,
                generation_count=1,
                filename_prefix="test",
                save_last_frame_batch=False,
                enable_offline_inference=False,
                non_blocking=True,
                draft_mode=True,
                reuse_last_draft_task=True,
                draft_task_id="",
            )
        self.assertIn("no draft to reuse", str(ctx.exception))


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

    async def _run(
        self, save_layers=False, output_dir=None, model="dola-seedream-5-0-pro"
    ):
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

        from byteplussdkarkruntime.resources.images.images import Images as SdkImages

        class Images:
            @staticmethod
            def generate(**kwargs):
                assert_matches_sdk(SdkImages.generate, kwargs)
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
                model,
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

    async def test_flash_layer_decomposition_uses_flash_model(self):
        calls, result = await self._run(model="dola-seedream-5-0-flash")
        self.assertEqual(calls[0]["model"], "dola-seedream-5-0-flash-260915")
        self.assertTrue(calls[0]["layer_decomposition"])
        self.assertEqual(json.loads(result[3])["model"], "dola-seedream-5-0-flash-260915")

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

        calls = []
        client = SimpleNamespace(
            ark=SimpleNamespace(images=SimpleNamespace(generate=lambda **kw: calls.append(kw))),
            check_quota=lambda *_a: None,
            update_usage=lambda *_a: None,
        )
        with self.assertRaises(Exception) as ctx:
            await nodes_image.BytePlusSeedreamLayers.execute(
                client, "dola-seedream-5-0-pro", torch.ones((1, 100, 100, 3))
            )
        self.assertIn("Layer decomposition input must be between", str(ctx.exception))
        self.assertEqual(calls, [])

    def test_runs_as_output_node(self):
        self.assertTrue(nodes_image.BytePlusSeedreamLayers.define_schema().is_output_node)


@requires_comfyui
class SdkContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_files_upload_matches_sdk(self):
        import tempfile
        import time
        from byteplussdkarkruntime.resources.files import Files as SdkFiles

        created = []

        class Files:
            @staticmethod
            def create(**kwargs):
                assert_matches_sdk(SdkFiles.create, kwargs)
                created.append(kwargs)
                return SimpleNamespace(id="file-1", status="active")

            @staticmethod
            def retrieve(**kwargs):
                assert_matches_sdk(SdkFiles.retrieve, kwargs)
                return SimpleNamespace(status="active", expire_at=int(time.time()) + 86400)

        client = SimpleNamespace(ark=SimpleNamespace(files=Files()))
        old_save = nodes_shared.save_files_upload_cache
        nodes_shared.save_files_upload_cache = lambda: None
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
                tmp.write(os.urandom(64))
                tmp.flush()
                file_id = await nodes_shared.upload_file_to_ark(client, tmp.name)
        finally:
            nodes_shared.save_files_upload_cache = old_save
        self.assertEqual(file_id, "file-1")
        self.assertIn("expires_at", created[0])
        self.assertNotIn("preprocess_configs", created[0])

        nodes_shared.save_files_upload_cache = lambda: None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
                tmp.write(os.urandom(64))
                tmp.flush()
                await nodes_shared.upload_file_to_ark(client, tmp.name, fps=2.0, model="seed-2-0-lite-260428")
        finally:
            nodes_shared.save_files_upload_cache = old_save
        self.assertEqual(
            created[-1]["preprocess_configs"], {"video": {"fps": 2.0, "model": "seed-2-0-lite-260428"}}
        )


@requires_comfyui
class QuotaSettingsTests(unittest.TestCase):
    def test_client_passthrough(self):
        quota = importlib.import_module(f"{PACKAGE_NAME}.nodes.quota")
        outputs = quota.BytePlusQuotaSettings.define_schema().outputs
        self.assertEqual([o.display_name for o in outputs], ["status", "client"])
        client = SimpleNamespace(api_key="test-key")
        result = quota.BytePlusQuotaSettings.execute(client, "seedream-4-0", 3, "None", 0)
        self.assertIs(result[1], client)
        self.assertIn("seedream-4-0-250828: 0/3 images", result[0])
        with self.assertRaises(Exception):
            quota.QuotaManager.instance().check_quota("test-key", "seedream-4-0-250828", 4)


@requires_comfyui
class AssetLibraryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assets = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_assets")
        self.calls = []
        self.statuses = ["Processing", "Active"]
        self.groups = []
        self.group_pages = []
        self.failures = {}
        self.empty_ids = False
        test = self

        class FakeLibrary:
            def __init__(self, client):
                test.assets.resolve_asset_credentials(client)
                self.client = client
                self.region = getattr(client, "region", "ap-southeast-1")
                self.account_fingerprint = "acct-1"

            def call(self, action, body):
                test.calls.append((action, body))
                failure = test.failures.get(action)
                if failure:
                    test.failures[action] -= 1
                    raise test.assets.BytePlusException(f"[BytePlus] {action} failed")
                if action == "ListAssetGroups":
                    if test.group_pages:
                        return test.group_pages.pop(0)
                    return {"Items": list(test.groups)}
                if action == "CreateAssetGroup":
                    return {"Id": "group-new"}
                if action == "CreateAsset":
                    if test.empty_ids:
                        return {}
                    return {"Id": f"asset-{len([c for c in test.calls if c[0] == 'CreateAsset'])}"}
                if action == "GetAsset":
                    status = test.statuses.pop(0) if len(test.statuses) > 1 else test.statuses[0]
                    return {"Id": body["Id"], "Status": status, "GroupId": "group-new", "AssetType": "Image"}
                if action == "ListAssets":
                    return {"Items": [{"Id": "asset-a", "Status": "Active"}, {"Id": "asset-b", "Status": "Active"}]}
                raise AssertionError(action)

        async def fake_upload(_cls, _image):
            return "https://storage.example/portrait.png"

        self._old = (self.assets.AssetLibrary, self.assets.upload_image_to_comfy_storage, self.assets.ASSET_POLL_SECONDS)
        self.assets.AssetLibrary = FakeLibrary
        self.assets.upload_image_to_comfy_storage = fake_upload
        self.assets.ASSET_POLL_SECONDS = 0
        self.assets.ASSET_UPLOAD_CACHE.clear()
        self.client = SimpleNamespace(
            region="ap-southeast-1",
            asset_credentials={"access_key": "AK", "secret_key": "SK", "session_token": ""},
        )

    def tearDown(self):
        (self.assets.AssetLibrary, self.assets.upload_image_to_comfy_storage, self.assets.ASSET_POLL_SECONDS) = self._old

    async def _create(self, **kwargs):
        import torch

        defaults = {"image": torch.ones((1, 16, 16, 3))}
        return await self.assets.BytePlusVirtualPortraitAsset.execute(self.client, **{**defaults, **kwargs})

    async def test_creates_group_and_asset_and_waits_until_active(self):
        result = await self._create(group_name="Neon Demo", asset_name="Neon portrait", project_name="default")
        actions = [a for a, _ in self.calls]
        self.assertEqual(actions, ["ListAssetGroups", "CreateAssetGroup", "CreateAsset", "GetAsset", "GetAsset"])
        self.assertEqual(self.calls[0][1]["Filter"], {"Name": "Neon Demo", "GroupType": "AIGC"})
        self.assertEqual(self.calls[1][1]["GroupType"], "AIGC")
        create = self.calls[2][1]
        self.assertEqual(create["GroupId"], "group-new")
        self.assertEqual(create["URL"], "https://storage.example/portrait.png")
        self.assertEqual(create["AssetType"], "Image")
        self.assertEqual(result[0], "asset://asset-1")
        self.assertEqual(result[2], "group-new")
        self.assertEqual(json.loads(result[3])["status"], "Active")

    async def test_reuses_existing_group_and_asset_on_rerun(self):
        self.groups = [{"Id": "group-7", "Name": "Neon Demo"}, {"Id": "group-8", "Name": "Neon Demo 2"}]
        first = await self._create(group_name="Neon Demo")
        second = await self._create(group_name="Neon Demo")
        self.assertEqual(first[0], second[0])
        self.assertEqual([a for a, _ in self.calls].count("CreateAsset"), 1)
        self.assertNotIn("CreateAssetGroup", [a for a, _ in self.calls])
        self.assertEqual(self.calls[1][1]["GroupId"], "group-7")

    async def test_ambiguous_group_name_and_group_id_override(self):
        self.groups = [{"Id": "g1", "Name": "Same"}, {"Id": "g2", "Name": "Same"}]
        with self.assertRaises(Exception) as ctx:
            await self._create(group_name="Same")
        self.assertIn("Set group_id", str(ctx.exception))
        self.calls.clear()
        await self._create(group_name="Same", group_id="group-liveness-1")
        self.assertEqual(self.calls[0][0], "CreateAsset")
        self.assertEqual(self.calls[0][1]["GroupId"], "group-liveness-1")

    async def test_failed_asset_and_url_validation(self):
        self.statuses = ["Failed"]
        with self.assertRaises(Exception) as ctx:
            await self._create()
        self.assertIn("failed processing or review", str(ctx.exception))
        with self.assertRaises(Exception) as ctx:
            await self._create(image=None, image_url="http://insecure.example/a.png")
        self.assertIn("HTTPS", str(ctx.exception))
        with self.assertRaises(Exception) as ctx:
            await self._create(image=None, image_url="")
        self.assertIn("Connect an image", str(ctx.exception))

    async def test_url_source_keeps_asset_type(self):
        self.calls.clear()
        await self._create(image=None, image_url="https://cdn.example/voice.mp3", asset_type="Audio", wait_until_active=False)
        create = next(body for action, body in self.calls if action == "CreateAsset")
        self.assertEqual(create["AssetType"], "Audio")
        self.assertEqual(create["URL"], "https://cdn.example/voice.mp3")
        self.assertNotIn("GetAsset", [a for a, _ in self.calls])

    async def test_cached_asset_deleted_in_console_is_recreated(self):
        await self._create(group_id="group-7")
        self.failures["GetAsset"] = 1  # the cached asset is gone
        self.statuses = ["Active"]
        result = await self._create(group_id="group-7")
        creates = [a for a, _ in self.calls].count("CreateAsset")
        self.assertEqual(creates, 2)
        self.assertEqual(result[0], "asset://asset-2")

    async def test_cache_key_includes_asset_name(self):
        await self._create(group_id="group-7", asset_name="first")
        await self._create(group_id="group-7", asset_name="second")
        self.assertEqual([a for a, _ in self.calls].count("CreateAsset"), 2)

    async def test_wait_tolerates_transient_status_errors(self):
        self.failures["GetAsset"] = 2
        self.statuses = ["Active"]
        result = await self._create(group_id="group-7")
        self.assertEqual(json.loads(result[3])["status"], "Active")
        self.failures["GetAsset"] = self.assets.ASSET_MAX_STATUS_ERRORS
        self.assets.ASSET_UPLOAD_CACHE.clear()
        with self.assertRaises(Exception) as ctx:
            await self._create(group_id="group-8")
        self.assertIn("GetAsset failed", str(ctx.exception))

    async def test_group_lookup_pages_before_creating(self):
        self.group_pages = [
            {"Items": [{"Id": "g-other", "Name": "Neon Demo 2"}], "NextToken": "page-2"},
            {"Items": [{"Id": "g-neon", "Name": "Neon Demo"}]},
        ]
        await self._create(group_name="Neon Demo")
        actions = [a for a, _ in self.calls]
        self.assertEqual(actions[:2], ["ListAssetGroups", "ListAssetGroups"])
        self.assertEqual(self.calls[1][1]["NextToken"], "page-2")
        self.assertNotIn("CreateAssetGroup", actions)
        self.assertEqual(next(b for a, b in self.calls if a == "CreateAsset")["GroupId"], "g-neon")

    async def test_empty_ids_are_errors(self):
        self.empty_ids = True
        with self.assertRaises(Exception) as ctx:
            await self._create(group_id="group-7")
        self.assertIn("returned no ID", str(ctx.exception))

    def test_error_parsing_for_http_200_and_odd_bodies(self):
        from byteplussdkcore.rest import ApiException

        business = ApiException(status=200, reason=str({"Code": "AccessDenied", "Message": "no rights"}))
        message = self.assets._format_asset_error("CreateAsset", business)
        self.assertIn("AccessDenied: no rights", message)
        self.assertIn("Advanced Creation Rights", message)
        odd = ApiException(status=500)
        odd.body = "null"
        self.assertIn("CreateAsset failed", self.assets._format_asset_error("CreateAsset", odd))
        throttled = ApiException(status=429)
        throttled.body = json.dumps({"ResponseMetadata": {"Error": {"Code": "FlowLimitExceeded", "Message": "slow down"}}})
        self.assertIn("rate-limited", self.assets._format_asset_error("CreateAsset", throttled))

    async def test_list_assets(self):
        uris, raw = await self.assets.BytePlusAssetLibrary.execute(
            self.client, group_type="AIGC", group_id="group-7", status="Active", name="", max_results=5
        )
        self.assertEqual(uris, "asset://asset-a\nasset://asset-b")
        body = self.calls[0][1]
        self.assertEqual(body["Filter"], {"GroupType": "AIGC", "GroupIds": ["group-7"], "Statuses": ["Active"]})
        self.assertEqual(body["MaxResults"], 5)

    def test_credentials_resolution(self):
        store = nodes_shared.ApiKeyStore("/nonexistent")
        store._items = [
            {"customName": "with-ak", "apiKey": "k", "accessKey": "AK1", "secretKey": "SK1"},
            {"customName": "no-ak", "apiKey": "k"},
        ]
        self.assertEqual(store.find_asset_credentials("with-ak")["access_key"], "AK1")
        self.assertIsNone(store.find_asset_credentials("no-ak"))
        old_env = {k: os.environ.pop(k, None) for k in ("BYTEPLUS_ACCESS_KEY", "BYTEPLUS_SECRET_KEY", "BYTEPLUS_ACCESSKEY", "BYTEPLUS_SECRETKEY")}
        try:
            with self.assertRaises(Exception) as ctx:
                self.assets.resolve_asset_credentials(SimpleNamespace(asset_credentials=None))
            self.assertIn("accessKey", str(ctx.exception))
            os.environ["BYTEPLUS_ACCESS_KEY"], os.environ["BYTEPLUS_SECRET_KEY"] = "AKENV", "SKENV"
            creds = self.assets.resolve_asset_credentials(SimpleNamespace(asset_credentials=None))
            self.assertEqual((creds["access_key"], creds["secret_key"]), ("AKENV", "SKENV"))
        finally:
            for k in ("BYTEPLUS_ACCESS_KEY", "BYTEPLUS_SECRET_KEY"):
                os.environ.pop(k, None)
            for k, v in old_env.items():
                if v is not None:
                    os.environ[k] = v

    def test_real_library_uses_region_host_and_formats_errors(self):
        from byteplussdkcore.rest import ApiException

        library = self._old[0](SimpleNamespace(region="eu-west-1", asset_credentials={"access_key": "AK", "secret_key": "SK"}))
        self.assertEqual(library._api.api_client.configuration.host, "ark.eu-west-1.byteplusapi.com")
        error = ApiException(status=403)
        error.body = json.dumps({"ResponseMetadata": {"Error": {"Code": "AccessDenied", "Message": "no permission"}}})
        message = self.assets._format_asset_error("CreateAsset", error)
        self.assertIn("AccessDenied: no permission", message)
        self.assertIn("Advanced Creation Rights", message)

    def test_comfy_image_upload_call_matches_comfyui(self):
        import inspect
        import torch

        try:
            from comfy_api_nodes.util import upload_image_to_comfyapi
        except Exception as e:  # pragma: no cover - depends on the ComfyUI checkout
            self.skipTest(f"comfy_api_nodes unavailable: {e}")
        inspect.signature(upload_image_to_comfyapi).bind(
            object, torch.ones((1, 4, 4, 3)), mime_type="image/png", wait_label=None, total_pixels=None
        )


@requires_comfyui
class SeedanceAssetReferenceTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, model_config, **inputs):
        captured = {}

        async def fake_common(_self, *args, **kwargs):
            captured["kwargs"] = kwargs
            return "ok"

        old_common = nodes_video.BytePlusVideoBase._common_generation_logic
        old_hidden = getattr(nodes_video.BytePlusSeedance2, "hidden", None)
        nodes_video.BytePlusVideoBase._common_generation_logic = fake_common
        nodes_video.BytePlusSeedance2.hidden = SimpleNamespace(unique_id="asset-ref", prompt={})
        try:
            await nodes_video.BytePlusSeedance2.execute(
                SimpleNamespace(),
                {"model_version": "dreamina-seedance-2-5", "prompt": "Image 1 is Neon. He waves.",
                 "duration": 5, "auto_duration": False, "resolution": "720p", "aspect_ratio": "1:1",
                 **model_config},
                **inputs,
            )
        finally:
            nodes_video.BytePlusVideoBase._common_generation_logic = old_common
            if old_hidden is None:
                delattr(nodes_video.BytePlusSeedance2, "hidden")
            else:
                nodes_video.BytePlusSeedance2.hidden = old_hidden
        return captured

    async def test_asset_image_and_audio_references(self):
        captured = await self._run(
            {}, ref_image_urls="asset://asset-20260924083944-c6vqr\n", ref_audio_urls="https://cdn.example/voice.mp3"
        )
        content = captured["kwargs"]["content"]
        self.assertIn(
            {"type": "image_url", "image_url": {"url": "asset://asset-20260924083944-c6vqr"}, "role": "reference_image"},
            content,
        )
        self.assertIn(
            {"type": "audio_url", "audio_url": {"url": "https://cdn.example/voice.mp3"}, "role": "reference_audio"},
            content,
        )

    async def test_invalid_reference_and_count_limits(self):
        with self.assertRaises(Exception) as ctx:
            await self._run({}, ref_image_urls="portrait.png")
        self.assertIn("asset://<asset_id>", str(ctx.exception))
        with self.assertRaises(Exception) as ctx:
            await self._run({}, ref_image_urls="http://insecure.example/portrait.png")
        self.assertIn("asset://<asset_id>", str(ctx.exception))
        # asset:// is matched regardless of case, also for video links
        captured = await self._run({"aspect_ratio": "adaptive", "task_type": "reference"}, ref_video_urls="ASSET://asset-video-1")
        self.assertIn(
            {"type": "video_url", "video_url": {"url": "ASSET://asset-video-1"}, "role": "reference_video"},
            captured["kwargs"]["content"],
        )
        with self.assertRaises(Exception) as ctx:
            await self._run(
                {"model_version": "dreamina-seedance-2-0", "duration": 5},
                ref_image_urls="\n".join(f"asset://asset-{i}" for i in range(10)),
            )
        self.assertIn("at most 9", str(ctx.exception))

    async def test_first_frame_conflicts_with_asset_images(self):
        import torch

        with self.assertRaises(Exception) as ctx:
            await self._run(
                {"aspect_ratio": "adaptive"},
                first_frame_image=torch.zeros((1, 720, 1280, 3)),
                ref_image_urls="asset://asset-1",
            )
        self.assertIn("First/last frame mode cannot be used together", str(ctx.exception))


@requires_comfyui
class ReviewFixTests(unittest.IsolatedAsyncioTestCase):
    async def _upload(self, files):
        import tempfile

        client = SimpleNamespace(ark=SimpleNamespace(files=files))
        old = (nodes_shared.save_files_upload_cache, nodes_shared.FILE_ACTIVE_POLL_SECONDS)
        nodes_shared.save_files_upload_cache = lambda: None
        nodes_shared.FILE_ACTIVE_POLL_SECONDS = 0
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
                tmp.write(os.urandom(64))
                tmp.flush()
                return await nodes_shared.upload_file_to_ark(client, tmp.name)
        finally:
            nodes_shared.save_files_upload_cache, nodes_shared.FILE_ACTIVE_POLL_SECONDS = old

    async def test_failed_file_is_deleted_and_error_not_rewrapped(self):
        deleted = []
        files = SimpleNamespace(
            create=lambda **kw: SimpleNamespace(id="file-9", status="processing"),
            retrieve=lambda **kw: SimpleNamespace(status="failed", error=SimpleNamespace(message="bad codec")),
            delete=lambda **kw: deleted.append(kw["file_id"]),
        )
        with self.assertRaises(Exception) as ctx:
            await self._upload(files)
        message = str(ctx.exception)
        self.assertIn("could not process file file-9: bad codec", message)
        self.assertNotIn("File upload", message)
        self.assertEqual(message.count("[BytePlus]"), 1)
        self.assertEqual(deleted, ["file-9"])

    async def test_sdk_errors_are_wrapped_once(self):
        def boom(**_kw):
            raise RuntimeError("connection reset")

        files = SimpleNamespace(create=boom, retrieve=None, delete=None)
        with self.assertRaises(Exception) as ctx:
            await self._upload(files)
        self.assertIn("File upload to ModelArk failed", str(ctx.exception))
        self.assertEqual(str(ctx.exception).count("[BytePlus]"), 1)

    def test_dependency_check_accepts_sdk_without_metadata(self):
        from importlib.metadata import PackageNotFoundError

        import importlib.util

        # The test package does not run the plugin's __init__.py; load it as a
        # package of its own to reach check_dependencies.
        name = f"{PACKAGE_NAME}_entry"
        plugin = sys.modules.get(name)
        if plugin is None:
            spec = importlib.util.spec_from_file_location(
                name, os.path.join(PLUGIN_ROOT, "__init__.py"), submodule_search_locations=[PLUGIN_ROOT]
            )
            plugin = importlib.util.module_from_spec(spec)
            sys.modules[name] = plugin
            spec.loader.exec_module(plugin)

        def missing(_name):
            raise PackageNotFoundError(_name)

        old = plugin.package_version
        plugin.package_version = missing
        try:
            self.assertTrue(plugin.check_dependencies())
        finally:
            plugin.package_version = old

    def test_visual_history_is_scoped_and_bounded(self):
        visual = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_visual")
        a = visual._conversation_owner(SimpleNamespace(api_key="k1", region="ap-southeast-1"))
        b = visual._conversation_owner(SimpleNamespace(api_key="k1", region="eu-west-1"))
        c = visual._conversation_owner(SimpleNamespace(api_key="k2", region="ap-southeast-1"))
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertNotIn("k1", json.dumps(a))


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


@requires_comfyui
class FileActiveWaitTests(unittest.IsolatedAsyncioTestCase):
    def _client(self, statuses):
        calls = []

        def retrieve(file_id):
            status = statuses[min(len(calls), len(statuses) - 1)]
            calls.append(file_id)
            if isinstance(status, Exception):
                raise status
            return SimpleNamespace(status=status, error=SimpleNamespace(message="bad codec"))

        return SimpleNamespace(ark=SimpleNamespace(files=SimpleNamespace(retrieve=retrieve))), calls

    async def asyncSetUp(self):
        self.old_poll = nodes_shared.FILE_ACTIVE_POLL_SECONDS
        nodes_shared.FILE_ACTIVE_POLL_SECONDS = 0

    async def asyncTearDown(self):
        nodes_shared.FILE_ACTIVE_POLL_SECONDS = self.old_poll

    async def test_failed_file_raises_with_reason(self):
        client, _ = self._client(["processing", "failed"])
        with self.assertRaises(nodes_shared.BytePlusException) as ctx:
            await nodes_shared.wait_for_file_active(client, "file-1")
        self.assertIn("bad codec", str(ctx.exception))

    async def test_times_out(self):
        client, _ = self._client(["processing"])
        with self.assertRaises(nodes_shared.BytePlusException) as ctx:
            await nodes_shared.wait_for_file_active(client, "file-1", max_wait_seconds=0)
        self.assertIn("not ready", str(ctx.exception))

    async def test_transient_errors_then_active(self):
        client, calls = self._client([RuntimeError("503"), RuntimeError("503"), "active"])
        self.assertTrue(await nodes_shared.wait_for_file_active(client, "file-1"))
        self.assertEqual(len(calls), 3)

    async def test_repeated_errors_give_up(self):
        client, calls = self._client([RuntimeError("503")])
        with self.assertRaises(nodes_shared.BytePlusException):
            await nodes_shared.wait_for_file_active(client, "file-1")
        self.assertEqual(len(calls), nodes_shared.FILE_ACTIVE_MAX_RETRIEVE_ERRORS)

    async def test_interrupt_stops_waiting(self):
        import comfy.model_management as mm

        client, _ = self._client(["processing"])
        mm.interrupt_current_processing(True)
        try:
            with self.assertRaises(mm.InterruptProcessingException):
                await nodes_shared.wait_for_file_active(client, "file-1")
        finally:
            mm.interrupt_current_processing(False)


@requires_comfyui
class VisualMultiTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_previous_response_is_per_node_and_key(self):
        nodes_visual = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_visual")
        payloads = []
        counter = iter(range(100))

        class FakeExecutor:
            def __init__(self, client):
                pass

            async def create_response_task(self, payload):
                payloads.append(payload)
                return "task"

            async def poll_response_result(self, task_id):
                return {"id": f"resp-{next(counter)}", "output": []}

        async def run(node_id, api_key, turns):
            nodes_visual.BytePlusVisualUnderstanding.hidden = SimpleNamespace(unique_id=node_id, prompt={})
            client = SimpleNamespace(api_key=api_key, ark=None)
            await nodes_visual.BytePlusVisualUnderstanding.execute(
                client, "dola-seed-2-1-turbo", "", "hi", 0, False, 86400, "auto", 1.0, turns=turns
            )
            return payloads[-1].get("previous_response_id")

        old_executor = nodes_visual.BytePlusVisualExecutor
        old_hidden = getattr(nodes_visual.BytePlusVisualUnderstanding, "hidden", None)
        nodes_visual.BytePlusVisualExecutor = FakeExecutor
        nodes_visual.LAST_RESPONSES.clear()
        try:
            self.assertIsNone(await run("a", "key-1", 2))      # first turn: nothing to continue
            self.assertIsNone(await run("b", "key-1", 2))      # other node: own conversation
            self.assertEqual(await run("a", "key-1", 2), "resp-0")
            self.assertIsNone(await run("a", "key-2", 2))      # other account: start over
            self.assertIsNone(await run("b", "key-1", 1))      # turns=1: always new
        finally:
            nodes_visual.BytePlusVisualExecutor = old_executor
            nodes_visual.LAST_RESPONSES.clear()
            if old_hidden is None:
                delattr(nodes_visual.BytePlusVisualUnderstanding, "hidden")
            else:
                nodes_visual.BytePlusVisualUnderstanding.hidden = old_hidden


@requires_comfyui
class ApiKeySavedEventTests(unittest.TestCase):
    def test_saving_custom_key_notifies_frontend(self):
        sent = []
        server = importlib.import_module("server")
        old_instance = getattr(server.PromptServer, "instance", None)
        server.PromptServer.instance = SimpleNamespace(
            send_sync=lambda event, data, sid=None: sent.append((event, data, sid)),
            client_id="browser-1",
            last_prompt_id="prompt-9",
        )
        saved = {}
        patches = {
            "validate_api_key": lambda key, url: True,
            "save_api_key": lambda name, key: saved.update({name: key}),
            "Ark": lambda **kwargs: SimpleNamespace(**kwargs),
        }
        old = {name: getattr(nodes_shared, name) for name in patches}
        for name, value in patches.items():
            setattr(nodes_shared, name, value)
        nodes_shared.BytePlusAPIClient.hidden = SimpleNamespace(unique_id="7")
        try:
            nodes_shared.BytePlusAPIClient.execute("Custom", "sk-123", "work")
        finally:
            for name, value in old.items():
                setattr(nodes_shared, name, value)
            server.PromptServer.instance = old_instance
            delattr(nodes_shared.BytePlusAPIClient, "hidden")
        self.assertEqual(saved, {"work": "sk-123"})
        import hashlib

        self.assertEqual(len(sent), 1)
        event, data, sid = sent[0]
        self.assertEqual(event, nodes_shared.API_KEY_SAVED_EVENT)
        self.assertEqual(sid, "browser-1")  # only the client that queued the prompt
        self.assertEqual(data["node"], "7")
        self.assertEqual(data["key_name"], "work")
        self.assertEqual(data["prompt_id"], "prompt-9")
        self.assertEqual(data["key_fingerprint"], hashlib.sha256(b"sk-123").hexdigest()[:16])
        self.assertEqual(data["store"], "modelark")
        self.assertNotIn("sk-123", json.dumps(data))
        hidden = nodes_shared.BytePlusAPIClient.define_schema().hidden
        self.assertIn(nodes_shared.comfy_io.Hidden.unique_id, hidden)


@requires_comfyui
class LocalVideoHelperTests(unittest.TestCase):
    def test_probe_last_frame_and_placeholder_without_opencv(self):
        import tempfile

        import av
        import numpy as np

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clip.mp4")
            with av.open(path, "w") as container:
                stream = container.add_stream("h264", rate=24)
                stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
                for i in range(72):
                    frame = np.zeros((48, 64, 3), np.uint8)
                    frame[..., 0] = i * 3
                    for packet in stream.encode(av.VideoFrame.from_ndarray(frame, format="rgb24")):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)

            info = nodes_shared.probe_video_file(path)
            self.assertEqual((info["fps"], info["frame_count"], info["video_codec"]), (24.0, 72, "h264"))
            last = nodes_shared.extract_last_frame_tensor(path)
            self.assertEqual(tuple(last.shape), (1, 48, 64, 3))
            self.assertAlmostEqual(float(last[0, ..., 0].mean()) * 255, 213, delta=4)

        self.assertEqual(nodes_shared.probe_video_file("/missing.mp4"), {})
        self.assertIsNone(nodes_shared.extract_last_frame_tensor("/missing.mp4"))
        self.assertEqual(nodes_shared.create_white_video(32, 16).get_dimensions(), (32, 16))


def _speech_modules():
    return (
        importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_speech"),
        importlib.import_module(f"{PACKAGE_NAME}.nodes.speech_api"),
        importlib.import_module(f"{PACKAGE_NAME}.nodes.audio_utils"),
    )


def _sine_audio(seconds=1.0, sample_rate=24000, channels=1):
    import math

    import torch

    t = torch.arange(int(seconds * sample_rate)) / sample_rate
    wave = 0.5 * torch.sin(2 * math.pi * 440 * t)
    return {"waveform": wave.repeat(channels, 1)[None], "sample_rate": sample_rate}


class FakeSpeechHTTP:
    """Replaces speech_api._send: records requests, returns queued responses."""

    def __init__(self, speech_api, responses):
        self.speech_api = speech_api
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, method, url, headers, body, timeout):
        self.calls.append(SimpleNamespace(method=method, url=url, headers=dict(headers), body=body))
        status, headers_out, payload = self.responses.pop(0)
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode()
        elif isinstance(payload, str):
            payload = payload.encode()
        return self.speech_api.SpeechResponse(status, headers_out, payload)


class SpeechTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.nodes, self.api, self.audio = _speech_modules()
        self._old_send = self.api._send
        self.client = self.api.SeedSpeechClient("speech-key-1")

    def tearDown(self):
        self.api._send = self._old_send

    def serve(self, *responses):
        fake = FakeSpeechHTTP(self.api, responses)
        self.api._send = fake
        return fake


@requires_comfyui
class SpeechClientTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.nodes, self.api, _ = _speech_modules()
        self.tmp = tempfile.TemporaryDirectory()
        self.store = nodes_shared.ApiKeyStore(os.path.join(self.tmp.name, "speech_api_keys.json"))
        self.notified = []
        self._old = (self.nodes.SPEECH_API_KEY_STORE, self.nodes._notify_api_key_saved)
        self.nodes.SPEECH_API_KEY_STORE = self.store
        self.nodes._notify_api_key_saved = lambda *args, **kwargs: self.notified.append((args, kwargs))
        self.nodes.BytePlusSpeechClient.hidden = SimpleNamespace(unique_id="5")

    def tearDown(self):
        self.nodes.SPEECH_API_KEY_STORE, self.nodes._notify_api_key_saved = self._old
        delattr(self.nodes.BytePlusSpeechClient, "hidden")
        self.tmp.cleanup()

    def test_speech_keys_live_in_their_own_file(self):
        self.assertTrue(self.api.SPEECH_API_KEYS_FILE.endswith("speech_api_keys.json"))
        self.assertNotEqual(self.api.SPEECH_API_KEYS_FILE, nodes_shared.API_KEYS_FILE)
        self.assertEqual(self.api.BytePlusSpeechClientType.io_type, "BYTEPLUS_SPEECH_CLIENT")
        with open(os.path.join(PLUGIN_ROOT, ".gitignore"), encoding="utf-8") as f:
            self.assertIn("speech_api_keys.json", f.read().split())

    def test_schema_and_custom_key_is_saved_and_notified(self):
        schema = self.nodes.BytePlusSpeechClient.define_schema()
        self.assertEqual([i.id for i in schema.inputs], ["new_api_key", "new_key_name", "key_name", "region"])
        key_name = next(i for i in schema.inputs if i.id == "key_name")
        self.assertEqual(key_name.options[-2:], [self.nodes.ENV_KEY_OPTION, "Custom"])
        region = next(i for i in schema.inputs if i.id == "region")
        self.assertEqual(region.options, ["ap-southeast-1"])

        client = self.nodes.BytePlusSpeechClient.execute("Custom", " sk-voice ", "voice").result[0]
        self.assertEqual(client.api_key, "sk-voice")
        self.assertEqual(client.base_url, "https://voice.ap-southeast-1.bytepluses.com")
        self.assertNotIn("sk-voice", repr(client))
        self.assertEqual(self.store.find_api_key("voice"), "sk-voice")
        (args, kwargs), = self.notified
        self.assertEqual(args, ("5", "voice", "sk-voice"))
        self.assertEqual(kwargs, {"store": "speech"})

        again = self.nodes.BytePlusSpeechClient.execute("voice").result[0]
        self.assertEqual(again.api_key, "sk-voice")

    def test_env_and_missing_keys(self):
        env = self.nodes.SPEECH_API_KEY_ENV
        old = os.environ.pop(env, None)
        try:
            with self.assertRaises(nodes_shared.BytePlusException):
                self.nodes.BytePlusSpeechClient.execute(self.nodes.ENV_KEY_OPTION)
            os.environ[env] = "env-key"
            client = self.nodes.BytePlusSpeechClient.execute(self.nodes.ENV_KEY_OPTION).result[0]
            self.assertEqual(client.api_key, "env-key")
        finally:
            os.environ.pop(env, None)
            if old is not None:
                os.environ[env] = old
        with self.assertRaises(nodes_shared.BytePlusException):
            self.nodes.BytePlusSpeechClient.execute("Custom", "  ")
        with self.assertRaises(nodes_shared.BytePlusException):
            self.nodes.BytePlusSpeechClient.execute("nope")

    def test_modelark_client_is_rejected(self):
        with self.assertRaisesRegex(nodes_shared.BytePlusException, "Speech Client"):
            self.api.require_speech_client(SimpleNamespace(api_key="ark", ark=None))


@requires_comfyui
class SeedAudioTests(SpeechTestBase):
    TEXT_ONLY = {"reference_mode": "text only"}

    def _wav_payload(self, **extra):
        wav = self.audio.audio_to_wav_bytes(_sine_audio(0.5, 24000))
        import base64

        return {"code": 0, "message": "", "audio": base64.b64encode(wav).decode(), "duration": 0.5,
                "original_duration": 0.6, "url": "https://cdn.example/a.wav", **extra}

    async def _run(self, **kwargs):
        defaults = {"model": "seed-audio-1.0", "text_prompt": "Hello there", "reference_mode": self.TEXT_ONLY}
        return await self.nodes.BytePlusSeedAudio.execute(self.client, **{**defaults, **kwargs})

    async def test_text_only_request_and_outputs(self):
        fake = self.serve((200, {"X-Tt-Logid": "log-1"}, self._wav_payload(subtitle={
            "text": "Hello there",
            "sentences": [{"text": "Hello there", "start_time": 0, "end_time": 480}],
        })))
        audio, subtitles, srt, duration, url = (await self._run(
            audio_format="mp3", sample_rate="44100", speech_rate=10, enable_subtitle=True, aigc_watermark=True,
        )).result
        call = fake.calls[0]
        self.assertEqual(call.url, "https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/create")
        self.assertEqual(call.headers["X-Api-Key"], "speech-key-1")
        self.assertTrue(call.headers["X-Api-Request-Id"])
        self.assertNotIn("Authorization", call.headers)
        self.assertEqual(call.body, {
            "model": "seed-audio-1.0",
            "text_prompt": "Hello there",
            "audio_config": {"format": "mp3", "sample_rate": 44100, "speech_rate": 10, "loudness_rate": 0,
                             "pitch_rate": 0, "enable_subtitle": True},
            "watermark": {"aigc_watermark": True},
        })
        self.assertEqual(audio["sample_rate"], 24000)
        self.assertEqual(tuple(audio["waveform"].shape), (1, 1, 12000))
        self.assertEqual(duration, 0.5)
        self.assertEqual(url, "https://cdn.example/a.wav")
        self.assertIn("00:00:00,000 --> 00:00:00,480\nHello there", srt)
        self.assertEqual(json.loads(subtitles)["segments"][0]["text"], "Hello there")

    async def test_references_keep_slot_order(self):
        fake = self.serve((200, {}, self._wav_payload()))
        await self._run(
            text_prompt="@Audio1 greets @Audio2, then @Audio3 answers",
            reference_mode={
                "reference_mode": "audio reference",
                "ref_audio_1_source": "en_female_stokie_uranus_bigtts",
                "ref_audio_2_source": "https://cdn.example/voice.mp3",
                "reference_audio_3": _sine_audio(2.0, 48000, channels=2),
            },
        )
        refs = fake.calls[0].body["references"]
        self.assertEqual(refs[0], {"speaker": "en_female_stokie_uranus_bigtts"})
        self.assertEqual(refs[1], {"audio_url": "https://cdn.example/voice.mp3"})
        self.assertEqual(list(refs[2]), ["audio_data"])
        import base64

        self.assertEqual(base64.b64decode(refs[2]["audio_data"])[:4], b"RIFF")

    async def test_image_reference(self):
        import torch

        fake = self.serve((200, {}, self._wav_payload()), (200, {}, self._wav_payload()))
        await self._run(reference_mode={"reference_mode": "image reference", "reference_image": torch.ones((1, 32, 32, 3))})
        self.assertEqual(list(fake.calls[0].body["references"][0]), ["image_data"])
        await self._run(reference_mode={"reference_mode": "image reference", "ref_image_url": "https://cdn.example/face.png"})
        self.assertEqual(fake.calls[1].body["references"], [{"image_url": "https://cdn.example/face.png"}])

    async def test_reference_validation(self):
        import torch

        self.serve()
        audio_mode = {"reference_mode": "audio reference"}
        image_mode = {"reference_mode": "image reference"}
        cases = [
            {"reference_mode": {**audio_mode, "ref_audio_2_source": "voice_a"}},             # gap before slot 2
            {"reference_mode": {**audio_mode, "ref_audio_1_source": "voice_a",
                                "reference_audio_1": _sine_audio()}},                          # both in one slot
            {"reference_mode": {**image_mode, "reference_image": torch.ones((1, 8, 8, 3)),
                                "ref_image_url": "https://x/i.png"}},
            {"reference_mode": {**audio_mode, "ref_audio_1_source": "ftp://x/a.wav"}},
            {"reference_mode": {**image_mode, "ref_image_url": "asset://asset-1"}},  # not a Seed Speech URL
            {"reference_mode": {**audio_mode, "reference_audio_1": _sine_audio(31.0, 8000)}},
            {"text_prompt": "x" * 3001},
            {"text_prompt": "   "},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaises(nodes_shared.BytePlusException):
                    await self._run(**kwargs)

    async def test_error_codes_are_readable(self):
        self.serve(
            (200, {"X-Tt-Logid": "log-7"}, {"code": 45000001, "message": "text_prompt is invalid"}),
            (401, {}, {"code": 45000010, "message": "Invalid X-Api-Key"}),
        )
        with self.assertRaises(nodes_shared.BytePlusException) as ctx:
            await self._run()
        message = str(ctx.exception)
        self.assertTrue(message.startswith("[BytePlus] "))
        self.assertIn("Invalid request parameters: text_prompt is invalid", message)
        self.assertIn("log-7", message)
        with self.assertRaisesRegex(nodes_shared.BytePlusException, "ModelArk API keys do not work"):
            await self._run()

    async def test_downloads_url_when_audio_is_missing(self):
        wav = self.audio.audio_to_wav_bytes(_sine_audio(0.25, 16000))
        fake = self.serve(
            (200, {}, {"code": 0, "url": "https://cdn.example/b.wav"}),
            (200, {}, wav),
        )
        audio, _, _, duration, _ = (await self._run()).result
        self.assertEqual((fake.calls[1].method, fake.calls[1].url), ("GET", "https://cdn.example/b.wav"))
        self.assertEqual(audio["sample_rate"], 16000)
        self.assertAlmostEqual(duration, 0.25, places=3)


@requires_comfyui
class SeedTTSTests(SpeechTestBase):
    async def _run(self, **kwargs):
        defaults = {"model": "seed-tts-2.0", "text": "Hi", "voice": "en_female_stokie_uranus_bigtts"}
        return await self.nodes.BytePlusSeedTTS.execute(self.client, **{**defaults, **kwargs})

    def _stream(self, *items):
        return "\n".join(json.dumps(item) for item in items)

    async def test_request_and_stream(self):
        import base64
        import struct

        pcm = struct.pack("<4h", 0, 16384, -16384, 32767)
        fake = self.serve((200, {}, self._stream(
            {"code": 0, "message": "", "data": base64.b64encode(pcm[:4]).decode()},
            {"code": 0, "message": "", "sentence": {"text": "Hi", "words": [
                {"word": "Hi", "startTime": 0.1, "endTime": 0.4}]}},
            {"code": 0, "message": "", "data": base64.b64encode(pcm[4:]).decode()},
            {"code": 20000000, "message": "ok", "data": None},
        )))
        audio, subtitles, srt = (await self._run(
            context_text="Speak slowly.", emotion="happy", pitch=3, sample_rate="16000",
            explicit_language="en", silence_duration=500, filter_markdown=True, enable_subtitle=True,
        )).result
        call = fake.calls[0]
        self.assertEqual(call.url, "https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/unidirectional")
        self.assertEqual(call.headers["X-Api-Resource-Id"], "seed-tts-2.0")
        self.assertEqual(call.headers["X-Api-App-Key"], "aGjiRDfUWi")
        params = call.body["req_params"]
        self.assertEqual(params["speaker"], "en_female_stokie_uranus_bigtts")
        self.assertEqual(params["audio_params"], {
            "format": "pcm", "sample_rate": 16000, "speech_rate": 0, "loudness_rate": 0,
            "emotion": "happy", "emotion_scale": 4, "enable_subtitle": True,
        })
        self.assertEqual(json.loads(params["additions"]), {
            "post_process": {"pitch": 3}, "context_texts": ["Speak slowly."], "explicit_language": "en",
            "silence_duration": 500, "disable_markdown_filter": True,
        })
        self.assertEqual(audio["sample_rate"], 16000)
        self.assertEqual(tuple(audio["waveform"].shape), (1, 1, 4))
        self.assertAlmostEqual(float(audio["waveform"][0, 0, 1]), 0.5)
        self.assertIn("00:00:00,100 --> 00:00:00,400\nHi", srt)
        self.assertEqual(json.loads(subtitles)["segments"][0]["start_ms"], 100)

    async def test_voice_rules(self):
        fake = self.serve((200, {}, self._stream({"code": 0, "data": "AAAA"}, {"code": 20000000})))
        await self._run(model="seed-icl-2.0", custom_speaker_id="S_abc123", sample_rate="48000")
        self.assertEqual(fake.calls[0].headers["X-Api-Resource-Id"], "seed-icl-2.0")
        self.assertEqual(fake.calls[0].body["req_params"]["speaker"], "S_abc123")
        self.assertEqual(json.loads(fake.calls[0].body["req_params"]["additions"]), {})
        for kwargs in ({"model": "seed-tts-1.0"}, {"sample_rate": "48000"}, {"text": " "}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(nodes_shared.BytePlusException):
                    await self._run(**kwargs)

    async def test_error_inside_stream(self):
        self.serve((200, {}, self._stream(
            {"code": 0, "data": "AAAA"},
            {"code": 45000000, "message": "speaker permission denied: get resource id: access denied"},
        )))
        with self.assertRaisesRegex(nodes_shared.BytePlusException, "voice is not available"):
            await self._run()

    async def test_live_auth_error_shape(self):
        # Body and headers as returned by the live endpoint for a wrong key.
        self.serve((401, {"X-Tt-Logid": "2026093015"}, {
            "header": {"reqid": "5CFD", "code": 45000010, "message": "Invalid X-Api-Key"},
        }))
        with self.assertRaises(nodes_shared.BytePlusException) as ctx:
            await self._run()
        message = str(ctx.exception)
        self.assertIn("HTTP 401, code 45000010", message)
        self.assertIn("ModelArk API keys do not work", message)
        self.assertIn("log ID: 2026093015", message)

    async def test_empty_stream_is_an_error(self):
        self.serve((200, {}, self._stream({"code": 20000000, "message": "ok"})))
        with self.assertRaisesRegex(nodes_shared.BytePlusException, "no audio"):
            await self._run()


@requires_comfyui
class SeedASRTests(SpeechTestBase):
    RESULT = {
        "audio_info": {"duration": 2927},
        "result": {
            "text": "To be or not to be.",
            "utterances": [
                {"text": "To be", "start_time": 80, "end_time": 550, "additions": {"speaker": "1"}},
                {"text": "or not to be.", "start_time": 630, "end_time": 1390, "additions": {"speaker": "2"}},
            ],
        },
    }

    def setUp(self):
        super().setUp()
        self._old_poll = self.nodes.SPEECH_ASR_POLL_SECONDS
        self.nodes.SPEECH_ASR_POLL_SECONDS = 0

    def tearDown(self):
        self.nodes.SPEECH_ASR_POLL_SECONDS = self._old_poll
        super().tearDown()

    async def _run(self, **kwargs):
        return await self.nodes.BytePlusSeedASR.execute(self.client, **{"model": "seed-asr-fast", **kwargs})

    async def test_fast_mode_with_audio_input(self):
        import base64
        import wave

        fake = self.serve((200, {"X-Api-Status-Code": "20000000"}, self.RESULT))
        text, utterances, srt, duration = (await self._run(
            audio=_sine_audio(1.0, 48000, channels=2), language="zh-CN", enable_speaker_info=True,
            hotwords="Hamlet, Ophelia\nHamlet",
        )).result
        call = fake.calls[0]
        self.assertEqual(call.url, "https://voice.ap-southeast-1.bytepluses.com/api/v3/auc/bigmodel/recognize/flash")
        self.assertEqual(call.headers["X-Api-Resource-Id"], "volc.seedasr.auc_turbo")
        self.assertEqual(call.headers["X-Api-Sequence"], "-1")
        audio = call.body["audio"]
        self.assertEqual({k: v for k, v in audio.items() if k != "data"}, {
            "format": "wav", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1, "language": "zh-CN",
        })
        with wave.open(io.BytesIO(base64.b64decode(audio["data"]))) as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getnframes()), (16000, 1, 16000))
        request = call.body["request"]
        self.assertEqual(request["model_name"], "bigmodel")
        self.assertTrue(request["show_utterances"] and request["enable_speaker_info"])
        self.assertEqual(json.loads(request["corpus"]["context"]),
                         {"hotwords": [{"word": "Hamlet"}, {"word": "Ophelia"}]})
        self.assertEqual(text, "To be or not to be.")
        self.assertEqual(len(json.loads(utterances)), 2)
        self.assertIn("00:00:00,630 --> 00:00:01,390\nSpeaker 2: or not to be.", srt)
        self.assertAlmostEqual(duration, 2.927)

    async def test_standard_mode_submits_and_polls_with_same_task_id(self):
        fake = self.serve(
            (200, {"X-Api-Status-Code": "20000000"}, {}),
            (200, {"X-Api-Status-Code": "20000002"}, {}),
            (200, {"X-Api-Status-Code": "20000001"}, {}),
            (200, {"X-Api-Status-Code": "20000000"}, self.RESULT),
        )
        text, _, srt, _ = (await self._run(model="seed-asr-2.0", audio_url="https://cdn.example/talk.mp3")).result
        self.assertEqual(text, "To be or not to be.")
        self.assertNotIn("Speaker", srt)
        self.assertEqual([c.url.rsplit("/", 1)[-1] for c in fake.calls], ["submit", "query", "query", "query"])
        self.assertEqual(len({c.headers["X-Api-Request-Id"] for c in fake.calls}), 1)
        self.assertEqual({c.headers["X-Api-Resource-Id"] for c in fake.calls}, {"volc.seedasr.auc"})
        self.assertEqual(fake.calls[0].body["audio"], {"url": "https://cdn.example/talk.mp3", "format": "mp3"})
        self.assertEqual(fake.calls[1].body, {})

    async def test_silent_audio_and_errors(self):
        self.serve(
            (200, {"X-Api-Status-Code": "20000003"}, {}),
            (200, {"X-Api-Status-Code": "45000151", "X-Api-Message": "bad format", "X-Tt-Logid": "L9"}, {}),
        )
        self.assertEqual((await self._run(audio_url="https://cdn.example/quiet.wav")).result, ("", "[]", "", 0.0))
        with self.assertRaisesRegex(nodes_shared.BytePlusException, "audio format is not supported"):
            await self._run(audio_url="https://cdn.example/x.wav")

    async def test_input_validation(self):
        self.serve()
        cases = [
            {},
            {"audio": _sine_audio(), "audio_url": "https://cdn.example/a.wav"},
            {"model": "seed-asr-2.0", "audio": _sine_audio()},
            {"audio_url": "asset://asset-1"},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaises(nodes_shared.BytePlusException):
                    await self._run(**kwargs)


@requires_comfyui
class SpeechAdvancedOptionTests(SpeechTestBase):
    """Every documented request option reaches the API in the documented shape."""

    async def test_seed_audio_implicit_watermark_and_pcm(self):
        import struct

        pcm = struct.pack("<3h", 0, 16384, -16384)
        import base64

        fake = self.serve((200, {}, {"code": 0, "audio": base64.b64encode(pcm).decode()}))
        audio, *_ = (await self.nodes.BytePlusSeedAudio.execute(
            self.client, "Hi", {"reference_mode": "text only"}, model="seed-audio-1.0", audio_format="pcm",
            sample_rate="16000", aigc_watermark=True, aigc_metadata=True, content_producer="Studio",
            produce_id="p-1", content_propagator=" ", propagate_id="d-9",
        )).result
        self.assertEqual(fake.calls[0].body["watermark"], {
            "aigc_watermark": True,
            "aigc_metadata": {"enable": True, "content_producer": "Studio", "produce_id": "p-1",
                              "propagate_id": "d-9"},
        })
        self.assertEqual(fake.calls[0].body["audio_config"], {
            "format": "pcm", "sample_rate": 16000, "speech_rate": 0, "loudness_rate": 0, "pitch_rate": 0,
        })
        self.assertEqual(audio["sample_rate"], 16000)
        self.assertEqual(tuple(audio["waveform"].shape), (1, 1, 3))

    def test_tts_text_handling_options(self):
        headers, body = self.nodes.build_tts_request(
            "seed-icl-2.0", "Hi $x^2$ (aside) 😀", "", custom_speaker_id="S_1", enable_subtitle=True,
            detect_language=True, context_language="es", read_emoji=True, read_latex=True,
            read_parentheses=True, unsupported_char_ratio=0.5, use_cache=True, tone_fidelity=True,
        )
        self.assertEqual(headers["X-Api-Resource-Id"], "seed-icl-2.0")
        self.assertTrue(body["req_params"]["audio_params"]["enable_subtitle"])
        self.assertEqual(json.loads(body["req_params"]["additions"]), {
            "disable_markdown_filter": True, "enable_latex_tn": True, "enable_language_detector": True,
            "context_language": "es", "disable_emoji_filter": True, "max_length_to_filter_parenthesis": 0,
            "unsupported_char_ratio_thresh": 0.5, "cache_config": {"text_type": 1, "use_cache": True},
            "tone_fidelity": True,
        })
        _, body = self.nodes.build_tts_request("seed-tts-1.0", "Hi", "", custom_speaker_id="en_1", enable_subtitle=True)
        params = body["req_params"]["audio_params"]
        self.assertTrue(params["enable_timestamp"])
        self.assertNotIn("enable_subtitle", params)
        with self.assertRaises(nodes_shared.BytePlusException):
            self.nodes.build_tts_request("seed-tts-2.0", "Hi", "en_female_stokie_uranus_bigtts", tone_fidelity=True)

    def test_asr_recognition_options(self):
        mode, _, body = self.nodes.build_asr_request(
            "seed-asr-2.0", audio_url="https://cdn.example/call.m4a", enable_lid=True,
            enable_channel_split=True, vad_segment=True, end_window_size=800, output_zh_variant="tw",
            filter_system_sensitive_words=True, remove_words="um, uh", mask_words="secret",
            wrap_sensitive_words=True, hotwords="BytePlus", context_text="bot: How can I help?\nuser: Billing.\nA support call.",
            context_image_url="https://cdn.example/slide.png",
        )
        self.assertEqual(mode, "standard")
        self.assertEqual(body["audio"], {"url": "https://cdn.example/call.m4a", "format": "m4a", "channel": 2})
        request = body["request"]
        for key, value in {"enable_lid": True, "enable_channel_split": True,
                           "vad_segment": True, "end_window_size": 800, "output_zh_variant": "tw"}.items():
            self.assertEqual(request[key], value, key)
        self.assertEqual(json.loads(request["sensitive_words_filter"]), {
            "system_reserved_filter": True, "filter_with_empty": ["um", "uh"],
            "filter_with_signed": ["secret"], "wrap_with_marks": True,
        })
        self.assertEqual(json.loads(request["corpus"]["context"]), {
            "hotwords": [{"word": "BytePlus"}],
            "context_type": "dialog_ctx",
            "context_data": [{"speaker": "bot", "text": "How can I help?"},
                             {"speaker": "user", "text": "Billing."}, {"text": "A support call."},
                             {"image_url": "https://cdn.example/slide.png"}],
        })
        _, _, plain = self.nodes.build_asr_request("seed-asr-fast", audio_url="https://cdn.example/a.wav")
        self.assertEqual(set(plain["request"]), {
            "model_name", "enable_itn", "enable_punc", "enable_ddc", "enable_speaker_info", "show_utterances",
        })

    def test_asr_option_validation(self):
        cases = [
            {"model": "seed-asr-fast", "audio_url": "https://x/a.wav", "enable_lid": True},
            {"model": "seed-asr-fast", "audio_url": "https://x/a.wav", "end_window_size": 200},
            {"model": "seed-asr-fast", "audio": _sine_audio(0.2), "enable_channel_split": True},
            {"model": "seed-asr-fast", "audio_url": "https://x/a.wav", "context_image_url": "asset://a"},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaises(nodes_shared.BytePlusException):
                    self.nodes.build_asr_request(**kwargs)

    async def test_asr_channel_split_keeps_stereo_and_labels_channels(self):
        import base64
        import wave

        fake = self.serve((200, {"X-Api-Status-Code": "20000000"}, {"result": {"text": "Hi. Hello.", "utterances": [
            {"text": "Hi.", "start_time": 0, "end_time": 400, "additions": {"channel_id": "1"}},
            {"text": "Hello.", "start_time": 500, "end_time": 900, "additions": {"channel_id": "2", "speaker": "1"}},
        ]}}))
        _, _, srt, _ = (await self.nodes.BytePlusSeedASR.execute(
            self.client, "seed-asr-fast", audio=_sine_audio(0.5, 16000, channels=2), enable_channel_split=True,
        )).result
        audio = fake.calls[0].body["audio"]
        self.assertEqual(audio["channel"], 2)
        with wave.open(io.BytesIO(base64.b64decode(audio["data"]))) as wav:
            self.assertEqual(wav.getnchannels(), 2)
        self.assertIn("Channel 1: Hi.", srt)
        self.assertIn("Channel 2 / Speaker 1: Hello.", srt)


@requires_comfyui
class SpeechUploadTests(SpeechTestBase):
    """Connected media for options that only take URLs goes through Comfy.org storage."""

    def setUp(self):
        super().setUp()
        self.uploads = []
        self._old_upload = self.nodes.upload_to_comfy_storage
        self._old_poll = self.nodes.SPEECH_ASR_POLL_SECONDS
        self.nodes.SPEECH_ASR_POLL_SECONDS = 0

        async def fake_upload(node_cls, kind, data, filename, mime_type):
            self.uploads.append((kind, data, filename, mime_type))
            return f"https://storage.example/{filename}"

        self.nodes.upload_to_comfy_storage = fake_upload

    def tearDown(self):
        self.nodes.upload_to_comfy_storage = self._old_upload
        self.nodes.SPEECH_ASR_POLL_SECONDS = self._old_poll
        super().tearDown()

    RESULT = {"result": {"text": "Hello.", "utterances": []}, "audio_info": [{"duration": 1000}]}

    async def test_standard_asr_uploads_connected_audio(self):
        import wave

        fake = self.serve(
            (200, {"X-Api-Status-Code": "20000000"}, {}),
            (200, {"X-Api-Status-Code": "20000000"}, self.RESULT),
        )
        text, _, _, duration = (await self.nodes.BytePlusSeedASR.execute(
            self.client, "seed-asr-2.0", audio=_sine_audio(1.0, 44100, channels=2),
        )).result
        (kind, data, filename, mime), = self.uploads
        self.assertEqual((kind, filename, mime), ("audio", "asr.wav", "audio/wav"))
        with wave.open(io.BytesIO(data)) as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels()), (16000, 1))
        self.assertEqual(fake.calls[0].body["audio"], {
            "url": "https://storage.example/asr.wav", "format": "wav", "codec": "raw", "rate": 16000, "bits": 16,
        })
        self.assertEqual((text, duration), ("Hello.", 1.0))  # audio_info as a list, like the docs

    async def test_context_image_is_uploaded_as_small_jpeg(self):
        import torch

        fake = self.serve((200, {"X-Api-Status-Code": "20000000"}, self.RESULT))
        noisy = torch.rand((1, 1600, 1600, 3))
        await self.nodes.BytePlusSeedASR.execute(
            self.client, "seed-asr-fast", audio=_sine_audio(), context_image=noisy,
        )
        (kind, data, filename, mime), = self.uploads
        self.assertEqual((kind, filename, mime), ("image", "context.jpg", "image/jpeg"))
        self.assertLessEqual(len(data), 500 * 1024)
        self.assertEqual(data[:2], b"\xff\xd8")
        context = json.loads(fake.calls[0].body["request"]["corpus"]["context"])
        self.assertEqual(context["context_data"], [{"image_url": "https://storage.example/context.jpg"}])

    async def test_upload_validation(self):
        import torch

        self.serve()
        image = torch.ones((1, 8, 8, 3))
        with self.assertRaises(nodes_shared.BytePlusException):
            await self.nodes.BytePlusSeedASR.execute(
                self.client, "seed-asr-1.0", audio_url="https://x/a.wav", context_image=image)
        with self.assertRaises(nodes_shared.BytePlusException):
            await self.nodes.BytePlusSeedASR.execute(
                self.client, "seed-asr-fast", audio_url="https://x/a.wav", context_image=image,
                context_image_url="https://x/i.png")
        with self.assertRaises(nodes_shared.BytePlusException):
            await self.nodes.BytePlusSeedASR.execute(
                self.client, "seed-asr-2.0", audio=_sine_audio(), audio_url="https://x/a.wav")
        self.assertEqual(self.uploads, [])

    async def test_comfy_storage_helper_caches_and_maps_errors(self):
        import comfy.model_management as mm

        calls = []
        behaviour = {"raise": None}

        async def upload_file_to_comfyapi(cls, file_bytes_io, filename, mime, wait_label=None):
            calls.append((filename, mime, file_bytes_io.getvalue()))
            if behaviour["raise"]:
                raise behaviour["raise"]
            return f"https://storage.example/{len(calls)}"

        fake_util = types.ModuleType("comfy_api_nodes.util")
        fake_util.upload_file_to_comfyapi = upload_file_to_comfyapi
        old_module = sys.modules.get("comfy_api_nodes.util")
        sys.modules["comfy_api_nodes.util"] = fake_util
        self.nodes.SPEECH_UPLOAD_CACHE.clear()
        upload = self._old_upload
        try:
            first = await upload(None, "audio", b"abc", "a.wav", "audio/wav")
            again = await upload(None, "audio", b"abc", "a.wav", "audio/wav")
            self.assertEqual((first, again, len(calls)), ("https://storage.example/1",) * 2 + (1,))
            behaviour["raise"] = RuntimeError("not logged in")
            with self.assertRaisesRegex(nodes_shared.BytePlusException, "Comfy.org"):
                await upload(None, "image", b"other", "i.jpg", "image/jpeg")
            behaviour["raise"] = mm.InterruptProcessingException()
            with self.assertRaises(mm.InterruptProcessingException):
                await upload(None, "image", b"third", "i.jpg", "image/jpeg")
        finally:
            self.nodes.SPEECH_UPLOAD_CACHE.clear()
            if old_module is None:
                sys.modules.pop("comfy_api_nodes.util", None)
            else:
                sys.modules["comfy_api_nodes.util"] = old_module


@requires_comfyui
class SeedVoiceCloneTests(SpeechTestBase):
    def setUp(self):
        super().setUp()
        self._old_poll = self.nodes.SEED_VOICE_POLL_SECONDS
        self.nodes.SEED_VOICE_POLL_SECONDS = 0

    def tearDown(self):
        self.nodes.SEED_VOICE_POLL_SECONDS = self._old_poll
        super().tearDown()

    async def _run(self, **kwargs):
        defaults = {"speaker_id": "S_abc123", "audio": _sine_audio(2.0, 48000, channels=2)}
        return await self.nodes.BytePlusSeedVoiceClone.execute(self.client, **{**defaults, **kwargs})

    async def test_uploads_clip_trains_and_returns_speaker(self):
        import base64
        import wave

        demo = self.audio.audio_to_wav_bytes(_sine_audio(0.5, 24000))
        fake = self.serve(
            (200, {}, {"code": 0, "speaker_id": "S_abc123", "status": 1}),
            (200, {}, {"code": 0, "status": 1}),
            (200, {}, {"code": 0, "status": 2, "speaker_status": [{"model_type": 5, "demo_audio": "https://cdn.example/demo.wav"}]}),
            (200, {}, demo),
        )
        speaker, demo_audio, status_json = (await self._run(
            language="en", reference_text="Hello there", demo_text="This is my cloned voice.",
            disable_volume_normalization=True,
        )).result
        clone = fake.calls[0]
        self.assertEqual(clone.url, "https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/voice_clone")
        self.assertEqual(clone.headers["X-Api-Key"], "speech-key-1")
        body = clone.body
        self.assertEqual((body["speaker_id"], body["language"], body["text"]), ("S_abc123", 1, "Hello there"))
        self.assertNotIn("custom_speaker_id", body)
        self.assertEqual(body["audio"]["format"], "wav")
        with wave.open(io.BytesIO(base64.b64decode(body["audio"]["data"]))) as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels()), (48000, 2))
        self.assertEqual(body["extra_params"], {"demo_text": "This is my cloned voice.", "disable_volume_normalization": True})
        self.assertEqual([c.url.rsplit("/", 1)[-1] for c in fake.calls[1:3]], ["get_voice", "get_voice"])
        self.assertEqual(fake.calls[1].body, {"speaker_id": "S_abc123"})
        self.assertEqual((fake.calls[3].method, fake.calls[3].url), ("GET", "https://cdn.example/demo.wav"))
        self.assertEqual(speaker, "S_abc123")
        self.assertEqual(demo_audio["sample_rate"], 24000)
        self.assertEqual(json.loads(status_json)["status"], 2)

    async def test_postpaid_custom_voice_id(self):
        fake = self.serve((200, {}, {"code": 0, "status": 4}))
        speaker, demo, _ = (await self._run(speaker_id="MyBrandVoice01")).result
        self.assertEqual(fake.calls[0].body["speaker_id"], "custom_speaker_id")
        self.assertEqual(fake.calls[0].body["custom_speaker_id"], "MyBrandVoice01")
        self.assertEqual(speaker, "MyBrandVoice01")
        self.assertEqual(tuple(demo["waveform"].shape)[:2], (1, 1))  # no demo: silence
        for bad in ("", "short", "en_voice_one", "voice_bigtts", "ICL_voice01", "1voice_abc", "voice-name_", "bad name 1"):
            with self.subTest(bad=bad):
                with self.assertRaises(nodes_shared.BytePlusException):
                    self.nodes.build_voice_clone_request(bad, _sine_audio())
        with self.assertRaises(nodes_shared.BytePlusException):
            self.nodes.build_voice_clone_request("S_abc123", _sine_audio(), demo_text="Hi")

    async def test_training_failure_and_error_codes(self):
        self.serve(
            (200, {}, {"code": 0, "status": 1}),
            (200, {}, {"code": 0, "status": 3, "message": "SNR too low"}),
            (400, {"X-Tt-Logid": "L1"}, {"code": 45001123, "message": "upload quota exhausted"}),
        )
        with self.assertRaisesRegex(nodes_shared.BytePlusException, "SNR too low"):
            await self._run()
        with self.assertRaisesRegex(nodes_shared.BytePlusException, "no training attempts left"):
            await self._run()


@requires_comfyui
class SpeechRobustnessTests(SpeechTestBase):
    def test_stream_parser_rejects_garbage_and_reads_arrays(self):
        self.assertEqual(list(self.api.iter_json_objects('[{"a":1},{"b":2}] {"c":3}')), [{"a": 1}, {"b": 2}, {"c": 3}])
        with self.assertRaises(nodes_shared.BytePlusException):
            list(self.api.iter_json_objects('{"code":0,"data":"AAAA"}\n{"code":0,"data":"AA\n{"code":45000000}'))

    async def test_bad_base64_is_a_plugin_error(self):
        self.serve((200, {}, {"code": 0, "audio": "not base64!!"}),
                   (200, {}, '{"code":0,"data":"A"}{"code":20000000}'))
        with self.assertRaisesRegex(nodes_shared.BytePlusException, r"^\[BytePlus\]"):
            await self.nodes.BytePlusSeedAudio.execute(self.client, "Hi", {"reference_mode": "text only"})
        with self.assertRaisesRegex(nodes_shared.BytePlusException, r"^\[BytePlus\]"):
            await self.nodes.BytePlusSeedTTS.execute(self.client, "seed-tts-2.0", "Hi", "en_female_stokie_uranus_bigtts")

    async def test_asr_rejects_bodies_without_result(self):
        self.serve((200, {}, "<html>gateway</html>"), (200, {"X-Api-Status-Code": "20000000"}, ""))
        for _ in range(2):
            with self.assertRaisesRegex(nodes_shared.BytePlusException, "unexpected response"):
                await self.nodes.BytePlusSeedASR.execute(self.client, "seed-asr-fast", audio_url="https://x/a.wav")

    def test_asr_format_language_and_context_rules(self):
        build = self.nodes.build_asr_request
        _, _, body = build("seed-asr-2.0", audio_url="https://x/talk.ogg")
        self.assertEqual(body["audio"], {"url": "https://x/talk.ogg", "format": "ogg", "codec": "opus"})
        _, _, body = build("seed-asr-2.0", audio_url="https://x/presigned?sig=1", audio_format="aac")
        self.assertEqual(body["audio"]["format"], "aac")
        _, _, body = build("seed-asr-fast", audio_url="https://x/talk.m4a")
        self.assertNotIn("format", body["audio"])
        _, _, body = build("seed-asr-2.0", audio_url="https://x/a.mp3", language="sk-SK")
        self.assertEqual(body["audio"]["language"], "sk-SK")
        cases = [
            {"model": "seed-asr-2.0", "audio_url": "https://x/presigned?sig=1"},        # format unknown
            {"model": "seed-asr-fast", "audio_url": "https://x/a", "audio_format": "m4a"},
            {"model": "seed-asr-fast", "audio_url": "https://x/a.wav", "language": "sk-SK"},
            {"model": "seed-asr-fast", "audio_url": "https://x/a.wav", "language": "en-US", "hotwords": "x"},
            {"model": "seed-asr-fast", "audio_url": "https://x/a.wav", "enable_auto_lang": True, "context_text": "x"},
            {"model": "seed-asr-1.0", "audio_url": "https://x/a.wav", "context_image_url": "https://x/i.png"},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(nodes_shared.BytePlusException):
                    build(**kwargs)

    def test_tts_context_text_is_tts_2_only(self):
        with self.assertRaises(nodes_shared.BytePlusException):
            self.nodes.build_tts_request("seed-tts-1.0", "Hi", "", custom_speaker_id="en_1", context_text="Slowly")

    async def test_interrupt_cancels_request_and_passes_through(self):
        import comfy.model_management as mm

        cancelled = asyncio.Event()

        async def slow_send(*_args):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        self.api._send = slow_send
        mm.interrupt_current_processing(True)
        try:
            with self.assertRaises(mm.InterruptProcessingException):
                await self.api.speech_post(self.client, "/x", {}, operation="test")
        finally:
            mm.interrupt_current_processing(False)
        await asyncio.wait_for(cancelled.wait(), 2)

    async def test_network_errors_are_readable(self):
        import aiohttp

        async def failing(*_args):
            raise aiohttp.ClientConnectionError("dns failure")

        self.api._send = failing
        with self.assertRaisesRegex(nodes_shared.BytePlusException, "Could not reach Seed Speech"):
            await self.api.speech_post(self.client, "/x", {}, operation="TTS")

    def test_speech_key_saved_event_and_failed_save(self):
        sent = []
        server = importlib.import_module("server")
        old_instance = getattr(server.PromptServer, "instance", None)
        server.PromptServer.instance = SimpleNamespace(
            send_sync=lambda event, data, sid=None: sent.append(data), client_id="c", last_prompt_id="p")
        try:
            nodes_shared._notify_api_key_saved("9", "voice", "sk-v", store="speech")
        finally:
            server.PromptServer.instance = old_instance
        self.assertEqual(sent[0]["store"], "speech")
        self.assertNotIn("sk-v", json.dumps(sent[0]))

        notified = []
        old = (self.nodes.SPEECH_API_KEY_STORE, self.nodes._notify_api_key_saved)
        self.nodes.SPEECH_API_KEY_STORE = SimpleNamespace(upsert=lambda name, key: False)
        self.nodes._notify_api_key_saved = lambda *a, **k: notified.append(a)
        self.nodes.BytePlusSpeechClient.hidden = SimpleNamespace(unique_id="9")
        try:
            client = self.nodes.BytePlusSpeechClient.execute("Custom", "sk-v", "voice").result[0]
        finally:
            self.nodes.SPEECH_API_KEY_STORE, self.nodes._notify_api_key_saved = old
            delattr(self.nodes.BytePlusSpeechClient, "hidden")
        self.assertEqual(client.api_key, "sk-v")
        self.assertEqual(notified, [])  # key stays in the node when it could not be saved

    def test_asr_builder_accepts_every_schema_input(self):
        import inspect

        params = set(inspect.signature(self.nodes.build_asr_request).parameters)
        schema_ids = {i.id for i in self.nodes.BytePlusSeedASR.define_schema().inputs}
        self.assertEqual(schema_ids - params, {"speech_client", "context_image"})


@requires_comfyui
class SpeechHelperTests(unittest.TestCase):
    def setUp(self):
        self.nodes, self.api, self.audio = _speech_modules()

    def test_audio_round_trip_and_srt(self):
        wav = self.audio.audio_to_wav_bytes(_sine_audio(0.1, 22050, channels=2))
        decoded = self.audio.decode_audio_bytes(wav)
        self.assertEqual(decoded["sample_rate"], 22050)
        self.assertEqual(tuple(decoded["waveform"].shape), (1, 2, 2205))
        with self.assertRaises(nodes_shared.BytePlusException):
            self.audio.decode_audio_bytes(b"not audio")
        srt = self.audio.build_srt([{"start_ms": 3723004, "end_ms": 3724000, "text": "Late", "speaker": ""}])
        self.assertEqual(srt, "1\n01:02:03,004 --> 01:02:04,000\nLate\n")

    def test_schemas_match_template_input_orders(self):
        sys.path.insert(0, PLUGIN_ROOT)
        from tests.test_workflow_templates import WorkflowTemplateTests

        # BytePlusSeedAudio (DynamicCombo) is checked in tests.test_core_style_seed.
        for node in (self.nodes.BytePlusSpeechClient,
                     self.nodes.BytePlusSeedTTS, self.nodes.BytePlusSeedASR,
                     self.nodes.BytePlusSeedVoiceClone):
            schema = node.define_schema()
            self.assertEqual(
                [item.id for item in schema.inputs],
                WorkflowTemplateTests.CURRENT_INPUT_ORDERS[schema.node_id],
                schema.node_id,
            )

    def test_stream_parser_accepts_concatenated_and_sse(self):
        items = list(self.api.iter_json_objects('{"a":1}{"b":2}\ndata: {"c":3}\n'))
        self.assertEqual(items, [{"a": 1}, {"b": 2}, {"c": 3}])

    def test_voice_catalog(self):
        voices = importlib.import_module(f"{PACKAGE_NAME}.nodes.seed_speech_voices")
        self.assertEqual(len(voices.TTS_2_VOICE_IDS), len(set(voices.TTS_2_VOICE_IDS)))
        self.assertIn(voices.DEFAULT_TTS_VOICE, voices.TTS_2_VOICE_IDS)
        self.assertGreater(len(voices.TTS_2_VOICE_IDS), 100)
        for voice in voices.TTS_2_VOICES:
            self.assertTrue(all(str(field).isascii() for field in voice), voice)
            self.assertTrue(voice[0].endswith("_bigtts"), voice)

    def test_speech_messages_exist_and_are_english(self):
        import re

        for name in ("nodes_speech.py", "speech_api.py", "audio_utils.py"):
            with open(os.path.join(PLUGIN_ROOT, "nodes", name), encoding="utf-8") as f:
                source = f.read()
            self.assertTrue(source.isascii(), name)
            for key in set(re.findall(r'(?:get_text|_plain|log_msg)\(\s*"([a-z0-9_]+)"', source)):
                self.assertIn(key, constants.MESSAGES, f"{name}: missing message {key}")


if __name__ == "__main__":
    unittest.main()
