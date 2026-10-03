import asyncio
import importlib
import io
import json
import re
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock


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
    import comfy.cli_args  # noqa: E402

    # Importing comfy.model_management probes CUDA unless this is set; the tests need no GPU
    # (CI installs CPU-only torch).
    comfy.cli_args.args.cpu = True

    PACKAGE_NAME = "byteplus_plugin_test"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [PLUGIN_ROOT]
        sys.modules[PACKAGE_NAME] = package

    models_config = importlib.import_module(f"{PACKAGE_NAME}.nodes.models_config")
    constants = importlib.import_module(f"{PACKAGE_NAME}.nodes.constants")
    executor = importlib.import_module(f"{PACKAGE_NAME}.nodes.executor")
    nodes_video = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_video")
    nodes_shared = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_shared")
    core_style = importlib.import_module(f"{PACKAGE_NAME}.nodes.core_style")


def setUpModule():
    # Hide the tester's own BYTEPLUS_* variables and user/.env (see tests/support.py).
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


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
        self.assertEqual(list(constants.REGION_BASE_URLS), ["ap-southeast-1", "eu-west-1"])
        self.assertEqual(constants.DEFAULT_REGION, "ap-southeast-1")

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


    def test_seed_visual_models(self):
        self.assertEqual(
            models_config.VISUAL_MODEL_MAP,
            {
                "dola-seed-2-1-turbo": "dola-seed-2-1-turbo-260628",
                "seed-2-0-pro": "seed-2-0-pro-260328",
                "seed-2-0-lite": "seed-2-0-lite-260428",
                "seed-2-0-mini": "seed-2-0-mini-260428",
            },
        )

    def test_deprecated_models_are_removed_everywhere(self):
        """Models BytePlus shut down on 2026-11-11: UI name -> (model ID, replacement)."""
        self.assertEqual(
            models_config.RETIRED_MODELS,
            {
                "seedance-1-5-pro": ("seedance-1-5-pro-251215", "dreamina-seedance-2-0-mini-260615"),
            },
        )
        current_ids = {
            *models_config.VIDEO_MODEL_MAP.values(),
            *models_config.VISUAL_MODEL_MAP.values(),
            *models_config.SEED_LLM_MODEL_MAP.values(),
        }
        for name, (model_id, replacement) in models_config.RETIRED_MODELS.items():
            with self.subTest(model=name):
                self.assertIn(replacement, current_ids)
                self.assertNotIn(model_id, current_ids)
                for mapping in (
                    models_config.VIDEO_MODEL_MAP,
                    models_config.VISUAL_MODEL_MAP,
                    models_config.SEED_LLM_MODEL_MAP,
                    models_config.SEEDANCE_1_MODELS,
                ):
                    self.assertNotIn(name, mapping)
                    self.assertNotIn(model_id, mapping)
                for options in (
                    models_config.SEED_LLM_UI_OPTIONS,
                    models_config.SEEDANCE_1_MODEL_OPTIONS,
                    models_config.SEEDANCE_1_FLF_MODEL_OPTIONS,
                ):
                    self.assertNotIn(name, options)
                    self.assertNotIn(model_id, options)
        self.assertEqual(models_config.SEED_LLM_NO_REASONING_EFFORT, ())
        # Audio-capable LLM options must exist in the model map (a rename would silently drop audio).
        for label in models_config.SEED_LLM_AUDIO_MODELS:
            self.assertIn(label, models_config.SEED_LLM_MODEL_MAP)
        # Video Query Tasks can still list the retired model's tasks, by its dated ID.
        self.assertEqual(models_config.QUERY_TASKS_MODEL_LIST[-1:], ["seedance-1-5-pro"])
        self.assertEqual(nodes_video.resolve_query_models("seedance-1-5-pro"), ["seedance-1-5-pro-251215"])


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
class ModelRegionTests(unittest.TestCase):
    def test_raise_if_model_unavailable_in_region(self):
        lite = models_config.SEEDREAM_5_MODEL_MAP["seedream-5-0-lite"]
        self.assertEqual(lite, "seedream-5-0-260128")
        self.assertEqual(models_config.MODEL_REGION_EXCLUSIONS, {lite: ("eu-west-1",)})

        with self.assertRaises(nodes_shared.BytePlusException) as ctx:
            core_style.raise_if_model_unavailable_in_region(SimpleNamespace(region="eu-west-1"), lite)
        message = str(ctx.exception)
        self.assertTrue(message.startswith("[BytePlus]"), message)
        self.assertIn("seedream-5-0-260128 is not available in eu-west-1", message)

        # Lite elsewhere, or a client without a region: fine.
        core_style.raise_if_model_unavailable_in_region(SimpleNamespace(region="ap-southeast-1"), lite)
        core_style.raise_if_model_unavailable_in_region(SimpleNamespace(), lite)
        # Every other model is available in eu-west-1.
        eu = SimpleNamespace(region="eu-west-1")
        for model_id in (
            models_config.SEEDREAM_5_MODEL_MAP["dola-seedream-5-0-pro"],
            models_config.SEEDREAM_5_MODEL_MAP["dola-seedream-5-0-flash"],
            *models_config.SEEDREAM_4_MODEL_MAP.values(),
            *models_config.VIDEO_MODEL_MAP.values(),
            *models_config.VISUAL_MODEL_MAP.values(),
            "",
        ):
            with self.subTest(model=model_id):
                core_style.raise_if_model_unavailable_in_region(eu, model_id)


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
class UploadCacheKeyTests(unittest.TestCase):
    """A trimmed or cropped video must not reuse the full video's upload (wrong footage, billed)."""

    def test_trim_and_crop_change_the_key(self):
        import tempfile
        from comfy_api.input_impl import VideoFromFile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clip.mp4")
            with open(path, "wb") as f:
                f.write(b"not decoded here")
            helper = nodes_video.BytePlusVideoBase()
            full = helper._build_comfy_video_upload_cache_key(VideoFromFile(path))
            trimmed = helper._build_comfy_video_upload_cache_key(VideoFromFile(path, start_time=2.0, duration=4.0))
            later = helper._build_comfy_video_upload_cache_key(VideoFromFile(path, start_time=3.0, duration=4.0))
            cropped = helper._build_comfy_video_upload_cache_key(VideoFromFile(path, crop=(0, 0, 320, 240)))
            same = helper._build_comfy_video_upload_cache_key(VideoFromFile(path, start_time=2.0, duration=4.0))
        self.assertEqual(len({full, trimmed, later, cropped}), 4)
        self.assertEqual(trimmed, same)


@requires_comfyui
class IgnoredFailureTests(unittest.TestCase):
    """Several nodes of one class: a failure blocks the outputs with its message (no white placeholder)."""

    def test_failure_blocks_outputs_with_its_message(self):
        from comfy_execution.graph_utils import ExecutionBlocker

        with mock.patch.object(executor, "PromptServer", SimpleNamespace(instance=None)):
            runner = executor.BytePlusGenerationExecutor(
                SimpleNamespace(ark=None, api_key=None), node_id="5", ignore_errors=True
            )
        runner._create_failure_json("The request was blocked by the content policy.", task_id="cgt-9")
        self.assertIn("cgt-9", runner.ignored_failure)
        video, frame, response = nodes_video.BytePlusVideoBase._ignored_failure_outputs(runner).args
        for blocked in (video, frame):
            self.assertIsInstance(blocked, ExecutionBlocker)
            self.assertIn("content policy", blocked.message)
        self.assertIn("cgt-9", json.loads(response)["error"])


@requires_comfyui
class ApiErrorFormatTests(unittest.TestCase):
    """format_api_error: the API's own code wins; text rules add detail or fill in."""

    def code_of(self, error):
        text = nodes_shared.format_api_error(error)
        match = re.search(r"\(Code: ([^)]+)\)", text)
        return match.group(1) if match else None

    def test_explicit_code_beats_a_text_rule(self):
        # "policy violation" alone maps to the video-copyright message; the task said audio.
        error = {"code": "OutputAudioSensitiveContentDetected.PolicyViolation",
                 "message": "The output audio was rejected: policy violation."}
        self.assertEqual(self.code_of(error), "OutputAudioSensitiveContentDetected.PolicyViolation")

    def test_text_rule_refines_the_same_code(self):
        error = {"code": "InvalidParameter", "message": "the task is determined as video editing, but ..."}
        self.assertEqual(self.code_of(error), "InvalidParameter.TaskTypeConstraint")

    def test_text_rule_fills_in_without_a_code(self):
        message = "Requests per minute (RPM) limit of the associated endpoint has been reached."
        self.assertEqual(self.code_of(message), "RateLimitExceeded.EndpointRPMExceeded")
        message = "Tokens per minute (TPM) limit of the associated endpoint has been reached."
        self.assertEqual(self.code_of(message), "RateLimitExceeded.EndpointTPMExceeded")

    def test_task_error_object_and_request_id(self):
        task = SimpleNamespace(error=SimpleNamespace(code="InvalidParameter.TaskTypeMismatch", message="mismatch"))
        self.assertEqual(self.code_of(executor._task_error(task, "mismatch")), "InvalidParameter.TaskTypeMismatch")
        self.assertEqual(executor._task_error(SimpleNamespace(error=None), "Expired"), "Expired")

        import httpx
        from byteplussdkarkruntime import _exceptions

        request = httpx.Request("POST", "https://ark.example/api/v3/responses")
        error = _exceptions.ArkBadRequestError(
            "Error code: 400", response=httpx.Response(400, request=request),
            body={"code": "InvalidParameter", "message": "bad"}, request_id="req-123",
        )
        text = nodes_shared.format_api_error(error)
        self.assertIn("InvalidParameter", text)
        self.assertIn("Request ID: req-123", text)


@requires_comfyui
class BilledCallTests(unittest.IsolatedAsyncioTestCase):
    """Calls that start paid work never get the SDK's automatic retries (duplicate billing)."""

    @staticmethod
    def _status_error(cls_name, status):
        import httpx
        from byteplussdkarkruntime import _exceptions

        request = httpx.Request("POST", "https://ark.example/api/v3/contents/generations/tasks")
        response = httpx.Response(status, request=request)
        return getattr(_exceptions, cls_name)("error", response=response, body=None, request_id="r1")


    def test_only_rate_limits_are_retried(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) < 3:
                raise self._status_error("ArkRateLimitError", 429)
            return "task-1"

        with mock.patch.object(nodes_shared.time, "sleep") as sleep:
            self.assertEqual(nodes_shared.call_billed(create, model="m"), "task-1")
        self.assertEqual(len(calls), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])

        # A server error or timeout may have started the work: never retried.
        for error in (self._status_error("ArkInternalServerError", 500), self._status_error("ArkConflictError", 409)):
            calls.clear()

            def failing(**kwargs):
                calls.append(kwargs)
                raise error

            with self.subTest(status=error.status_code), self.assertRaises(type(error)):
                nodes_shared.call_billed(failing, model="m")
            self.assertEqual(len(calls), 1)

        # Rate limited on every attempt: gives up after two retries.
        calls.clear()

        def limited(**kwargs):
            calls.append(kwargs)
            raise self._status_error("ArkRateLimitError", 429)

        with mock.patch.object(nodes_shared.time, "sleep"), self.assertRaises(Exception):
            nodes_shared.call_billed(limited)
        self.assertEqual(len(calls), 3)

    async def test_llm_request_can_be_interrupted(self):
        import threading
        import time as _time
        import comfy.model_management as mm

        release = threading.Event()

        def slow_create(**payload):
            release.wait(10)  # a long max-effort answer
            return SimpleNamespace(id="resp-1")

        client = SimpleNamespace(ark=None, billed_ark=SimpleNamespace(responses=SimpleNamespace(create=slow_create)),
                                 api_key="k")
        runner = executor.BytePlusVisualExecutor(client)

        async def interrupt_soon():
            await asyncio.sleep(0.3)
            mm.interrupt_current_processing(True)

        started = _time.monotonic()
        try:
            waiter = asyncio.ensure_future(interrupt_soon())
            with self.assertRaises(mm.InterruptProcessingException):
                await runner.create_response_task({"model": "m", "input": "hi"})
            await waiter
        finally:
            mm.interrupt_current_processing(False)
            release.set()
        self.assertLess(_time.monotonic() - started, 5)

    def test_executors_use_the_no_retry_client(self):
        # Task creation and LLM responses go through billed_ark; polling stays on the plain client.
        client = SimpleNamespace(ark=object(), billed_ark=object(), api_key=None)
        with mock.patch.object(executor, "PromptServer", SimpleNamespace(instance=None)):
            runner = executor.BytePlusGenerationExecutor(client)
        self.assertIs(runner.billed_ark, client.billed_ark)
        self.assertIs(runner.ark_client, client.ark)
        self.assertIs(executor.BytePlusVisualExecutor(client).ark_client, client.billed_ark)


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
        """Create Image Asset; outputs reordered as (asset_uri, asset_id, group_id, info)."""
        import torch

        node = self.assets.BytePlusCreateImageAsset
        node.hidden = SimpleNamespace(unique_id="1", prompt={})
        defaults = {"image": torch.ones((1, 320, 320, 3))}
        result = await node.execute(self.client, **{**defaults, **kwargs})
        asset_id, group_id, asset_uri, info = result.args
        return asset_uri, asset_id, group_id, info

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
        self.assertIn("Connect the image input", str(ctx.exception))


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


@requires_comfyui
class ComfyStorageUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_api_nodes_explains_alternatives(self):
        old_module = sys.modules.get("comfy_api_nodes.util", ...)
        sys.modules["comfy_api_nodes.util"] = None
        try:
            with self.assertRaises(Exception) as ctx:
                await nodes_video.upload_video_to_comfy_storage(
                    object, object(),
                    unavailable_key="err_comfy_upload_unavailable_reference",
                    failed_key="err_comfy_upload_failed_reference",
                )
        finally:
            if old_module is ...:
                sys.modules.pop("comfy_api_nodes.util", None)
            else:
                sys.modules["comfy_api_nodes.util"] = old_module
        self.assertIn("asset_N", str(ctx.exception))
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
                await nodes_video.upload_video_to_comfy_storage(
                    object, object(),
                    unavailable_key="err_comfy_upload_unavailable_reference",
                    failed_key="err_comfy_upload_failed_reference",
                )
        finally:
            if old_module is ...:
                sys.modules.pop("comfy_api_nodes.util", None)
            else:
                sys.modules["comfy_api_nodes.util"] = old_module
        message = str(ctx.exception)
        self.assertIn("401 Unauthorized", message)
        self.assertIn("Comfy.org", message)
        self.assertIn("asset_N", message)


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
class LocalVideoHelperTests(unittest.TestCase):
    def test_last_frame_without_opencv(self):
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

            last = nodes_shared.extract_last_frame_tensor(path)
            self.assertEqual(tuple(last.shape), (1, 48, 64, 3))
            self.assertAlmostEqual(float(last[0, ..., 0].mean()) * 255, 213, delta=4)

        self.assertIsNone(nodes_shared.extract_last_frame_tensor("/missing.mp4"))


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
class AssetCreateRetryTests(unittest.TestCase):
    def test_create_actions_use_a_client_without_retries(self):
        assets = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_assets")
        client = SimpleNamespace(region="ap-southeast-1", asset_credentials={"access_key": "AK", "secret_key": "SK"})
        library = assets.AssetLibrary(client)
        self.assertTrue(library._api.api_client._base_auto_retry)
        self.assertFalse(library._create_api.api_client._base_auto_retry)
        used = []
        library._api = SimpleNamespace(do_call=lambda info, body: used.append(("read", info.action)) or {})
        library._create_api = SimpleNamespace(do_call=lambda info, body: used.append(("create", info.action)) or {})
        library.call("GetAsset", {"Id": "a"})
        library.call("CreateAsset", {"GroupId": "g"})
        library.call("CreateAssetGroup", {"Name": "n"})
        self.assertEqual(used, [("read", "GetAsset"), ("create", "CreateAsset"), ("create", "CreateAssetGroup")])


@requires_comfyui
class SpeechPollTests(SpeechTestBase):
    """Status queries of a submitted task survive transient failures; other errors stop at once."""

    async def test_transient_failures_are_retried(self):
        fake = self.serve((503, {}, "busy"), (200, {"X-Api-Status-Code": "20000000"}, {"result": {}}))
        response = await self.api.speech_poll(self.client, "/api/v3/auc/bigmodel/query", {}, operation="ASR", poll_seconds=0)
        self.assertEqual(response.status, 200)
        self.assertEqual(len(fake.calls), 2)

    async def test_client_errors_are_not_retried(self):
        fake = self.serve((403, {}, {"header": {"code": 45000030, "message": "forbidden"}}))
        with self.assertRaises(nodes_shared.BytePlusException):
            await self.api.speech_poll(self.client, "/api/v3/auc/bigmodel/query", {}, operation="ASR", poll_seconds=0)
        self.assertEqual(len(fake.calls), 1)

    async def test_gives_up_after_repeated_failures(self):
        import aiohttp

        calls = []

        async def down(method, url, headers, body, timeout):
            calls.append(url)
            raise aiohttp.ClientConnectionError("reset")

        self.api._send = down
        with self.assertRaisesRegex(nodes_shared.BytePlusException, "ASR"):
            await self.api.speech_poll(self.client, "/q", {}, operation="ASR", poll_seconds=0)
        self.assertEqual(len(calls), self.api.SPEECH_POLL_MAX_ERRORS)


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
        outputs = (await self._run(
            audio_format="mp3", sample_rate="44100", speech_rate=10, enable_subtitle=True, aigc_watermark=True,
        )).result
        self.assertEqual([len(column) for column in outputs], [1] * 5)
        audio, subtitles, srt, duration, url = (column[0] for column in outputs)
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

    async def test_generation_count_runs_parallel_requests_into_list_outputs(self):
        fake = self.serve(*[(200, {}, self._wav_payload())] * 3)
        outputs = (await self._run(generation_count=3)).result
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(len({call.headers["X-Api-Request-Id"] for call in fake.calls}), 3)
        self.assertEqual([len(column) for column in outputs], [3] * 5)
        self.assertEqual(outputs[3], [0.5, 0.5, 0.5])

    async def test_generation_count_keeps_clips_when_some_requests_fail(self):
        self.serve(
            (200, {}, self._wav_payload()),
            (200, {}, {"code": 45000001, "message": "bad"}),
        )
        outputs = (await self._run(generation_count=2)).result
        self.assertEqual([len(column) for column in outputs], [1] * 5)

    async def test_generation_count_raises_when_every_request_fails(self):
        self.serve(*[(200, {}, {"code": 45000001, "message": "bad"})] * 2)
        with self.assertRaises(self.nodes.BytePlusException):
            await self._run(generation_count=2)

    async def test_downloads_url_when_audio_is_missing(self):
        wav = self.audio.audio_to_wav_bytes(_sine_audio(0.25, 16000))
        fake = self.serve(
            (200, {}, {"code": 0, "url": "https://cdn.example/b.wav"}),
            (200, {}, wav),
        )
        audio, _, _, duration, _ = (column[0] for column in (await self._run()).result)
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
        audio = (await self.nodes.BytePlusSeedAudio.execute(
            self.client, "Hi", {"reference_mode": "text only"}, model="seed-audio-1.0", audio_format="pcm",
            sample_rate="16000", aigc_watermark=True, aigc_metadata=True, content_producer="Studio",
            produce_id="p-1", content_propagator=" ", propagate_id="d-9",
        )).result[0][0]
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


    def test_asr_builder_accepts_every_schema_input(self):
        import inspect

        params = set(inspect.signature(self.nodes.build_asr_request).parameters)
        schema_ids = {i.id for i in self.nodes.BytePlusSeedASR.define_schema().inputs}
        self.assertEqual(schema_ids - params, {"context_image"})


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
        for node in (self.nodes.BytePlusSeedTTS, self.nodes.BytePlusSeedASR,
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
            for key in set(re.findall(r'(?:get_text|plain_text|log_msg)\(\s*"([a-z0-9_]+)"', source)):
                self.assertIn(key, constants.MESSAGES, f"{name}: missing message {key}")


if __name__ == "__main__":
    unittest.main()
