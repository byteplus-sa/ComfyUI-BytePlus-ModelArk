import os
import time
import random
import datetime
import asyncio
import aiohttp
import json
import logging
import base64
import hashlib

import comfy.model_management
from server import PromptServer
import torch

from comfy_api.latest import io as comfy_io
from comfy_api.input_impl import VideoFromFile

from .audio_utils import audio_to_wav_bytes, audio_waveform
from comfy_execution.graph_utils import ExecutionBlocker

from .nodes_shared import (
    GLOBAL_CATEGORY,
    _image_to_base64,
    log_msg,
    get_text,
    format_api_error,
    get_client,
    with_client,
    BytePlusException,
    get_node_count_in_workflow,
    extract_last_frame_tensor,
    safe_cat_tensors,
    wait_interruptible,
)
from .constants import (
    VIDEO_MAX_SEED,
    IMAGE_MIN_EDGE,
    IMAGE_MAX_EDGE,
    IMAGE_MIN_RATIO,
    IMAGE_MAX_RATIO,
    REF_IMAGE_MAX_SIZE_MB,
    REF_IMAGE_MAX_TOTAL_REQUEST_MB,
    REF_AUDIO_MIN_DURATION,
    REF_AUDIO_MAX_DURATION,
    REF_AUDIO_MAX_SIZE_MB,
    VIDEO_FRAME_RATE,
    VIDEO_MIN_FRAMES,
    VIDEO_MAX_FRAMES,
    VIDEO_FRAME_STEP,
    VIDEO_BASE_FRAMES,
)
from .utils_download import (
    download_video_to_temp,
    download_image_to_temp,
)

from .executor import (
    BytePlusGenerationExecutor,
    _get_api_estimated_time_async,
    _update_node_progress,
    _finish_node_progress,
    HISTORY_PAGE_SIZE,
)
from .models_config import (
    QUERY_TASKS_MODEL_LIST,
    RETIRED_MODELS,
    VIDEO_1_UI_OPTIONS,
    VIDEO_2_UI_OPTIONS,
    VIDEO_MODEL_MAP,
    VIDEO_2_MODEL_RESOLUTIONS,
    VIDEO_2_MODEL_MAX_DURATIONS,
    SEEDANCE_2_5_FAMILY,
    SEEDANCE_DRAFT_RESOLUTION,
)

logging.getLogger("byteplussdkarkruntime").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)


def resolve_model_id(model_version: str) -> str:
    """
    Map the model name selected in the UI to its dated ModelArk model ID.
    """
    if model_version in VIDEO_MODEL_MAP:
        return VIDEO_MODEL_MAP[model_version]

    raise BytePlusException(f"Model ID not found for selection: {model_version}")

def resolve_query_models(model_version: str) -> list:
    """
    Model IDs to query in the task list node; [None] means all models.
    """
    target_models = []
    if model_version == "all":
        target_models = [None]
    elif model_version in VIDEO_MODEL_MAP:
        target_models.append(VIDEO_MODEL_MAP[model_version])
    elif model_version in RETIRED_MODELS:
        target_models.append(RETIRED_MODELS[model_version][0])
    else:
        target_models.append(model_version)
    
    return target_models

def _calculate_duration_and_frames_args(duration: float):
    """
    Choose the API duration or frames argument.
    Whole seconds use duration; fractional seconds are converted to frames.
    """
    if duration == int(duration):
        return ("duration", int(duration), int(duration))
    else:
        target_frames = duration * VIDEO_FRAME_RATE
        n = round((target_frames - VIDEO_BASE_FRAMES) / VIDEO_FRAME_STEP)
        final_frames = int(max(VIDEO_MIN_FRAMES, min(VIDEO_MAX_FRAMES, VIDEO_BASE_FRAMES + VIDEO_FRAME_STEP * n)))
        return ("frames", final_frames, int(round(final_frames / VIDEO_FRAME_RATE)))


NON_BLOCKING_TASK_CACHE = {}
COMFY_VIDEO_UPLOAD_CACHE = {}
# Comfy.org deletes uploads after about 24h; reuse links for half of that so a
# cached link never expires while ModelArk is still fetching it.
COMFY_VIDEO_UPLOAD_CACHE_TTL_SECONDS = 43200
COMFY_VIDEO_UPLOAD_CACHE_MAX_ENTRIES = 256


async def upload_video_to_comfy_storage(
    node_cls,
    video,
    unavailable_key="err_comfy_upload_unavailable",
    failed_key="err_comfy_upload_failed",
) -> str:
    """
    Upload a reference video to Comfy.org storage and return its public URL.

    Seedance only accepts reference videos as URLs, so local videos go through
    ComfyUI's API-node upload helper. That helper is internal to ComfyUI, needs
    a Comfy.org login or API key, and is missing when API nodes are disabled.
    The message keys name the bypass input of the calling node.
    """
    try:
        from comfy_api_nodes.util import upload_video_to_comfyapi
    except Exception as e:
        raise BytePlusException(get_text(unavailable_key, e=e))
    try:
        return await upload_video_to_comfyapi(node_cls, video, wait_label=None)
    except comfy.model_management.InterruptProcessingException:
        raise
    except Exception as e:
        raise BytePlusException(get_text(failed_key, e=e))


async def upload_videos_to_comfy_storage_cached(node_cls, videos, helper=None, **message_keys):
    """
    Upload local videos to Comfy.org storage, reusing links cached for the
    same file or buffer (COMFY_VIDEO_UPLOAD_CACHE). Returns URLs in order.
    """
    helper = helper or BytePlusVideoBase()
    uploaded_video_urls = []
    for v in videos:
        cache_key = helper._build_comfy_video_upload_cache_key(v)
        cached_video_url = helper._get_cached_comfy_video_url(cache_key)
        if cached_video_url:
            uploaded_video_urls.append(cached_video_url)
            log_msg("upload_ref_video_cache_hit")
            continue
        done_before = len(uploaded_video_urls)
        pending_before = max(0, len(videos) - done_before)
        log_msg("upload_ref_video_start", done=done_before, pending=pending_before)
        uploaded_video_url = await upload_video_to_comfy_storage(node_cls, v, **message_keys)
        helper._save_cached_comfy_video_url(cache_key, uploaded_video_url)
        uploaded_video_urls.append(uploaded_video_url)
        log_msg("upload_ref_video_done")
    return uploaded_video_urls


def _parse_draft_task_ids(text) -> list[str]:
    return [line.strip() for line in str(text or "").replace(",", "\n").splitlines() if line.strip()]


def build_draft_final_content(draft_ids, generation_count):
    """Content and task count for final videos rendered from draft tasks."""
    drafts = [[{"type": "draft_task", "draft_task": {"id": tid}}] for tid in draft_ids]
    if len(drafts) == 1:
        return drafts[0], generation_count
    return drafts, len(drafts)


    # With auto the model decides the task type; any conflict is reported by
    # the API (InvalidParameter.TaskTypeConstraint).


def _raise_if_text_params(prompt: str, text_params: list[str]) -> None:
    for i in text_params:
        if f"--{i}" in prompt:
            raise BytePlusException(get_text("popup_param_not_allowed").format(param=i))


def _get_dynamic_input_order(name: str) -> int:
    parts = str(name).rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return int(parts[1])
    return 999


def validate_seedance2_resolution(model_version, resolution, draft_mode=False):
    supported_resolutions = VIDEO_2_MODEL_RESOLUTIONS.get(model_version)
    if supported_resolutions is None:
        raise BytePlusException(
            get_text("err_model_not_supported").format(model=model_version)
        )
    if draft_mode and model_version in SEEDANCE_2_5_FAMILY:
        supported_resolutions = [SEEDANCE_DRAFT_RESOLUTION]
    if resolution not in supported_resolutions:
        raise BytePlusException(
            get_text("err_seedance2_resolution_unsupported").format(
                model=model_version,
                resolution=resolution,
                supported=", ".join(supported_resolutions),
            )
        )
    return resolution


def validate_seedance2_duration(model_version, duration, auto_duration=False):
    max_duration = VIDEO_2_MODEL_MAX_DURATIONS.get(model_version)
    if max_duration is None:
        raise BytePlusException(
            get_text("err_model_not_supported").format(model=model_version)
        )
    if auto_duration:
        return -1
    try:
        duration_value = float(duration)
    except (TypeError, ValueError):
        duration_value = -1.0
    if (
        not duration_value.is_integer()
        or duration_value < 4
        or duration_value > max_duration
    ):
        raise BytePlusException(
            get_text("err_seedance2_duration_unsupported").format(
                model=model_version,
                min=4,
                max=max_duration,
                duration=duration,
            )
        )
    return int(duration_value)


class BytePlusVideoBase:
    """
    Base class for the Seedance nodes.
    Shared task submission, result handling and reference-media helpers.
    """
    NON_BLOCKING_TASK_CACHE = NON_BLOCKING_TASK_CACHE

    def _get_service_options(self, enable_offline, timeout_seconds):
        service_tier = "flex" if enable_offline else "default"
        execution_expires_after = timeout_seconds
        return service_tier, execution_expires_after

    def _append_image_content(self, content_list, image, role):
        if image is not None:
            image_b64 = _image_to_base64(image)
            image_b64_size_mb = float(len(image_b64.encode("utf-8"))) / (1024.0 * 1024.0)
            if image_b64_size_mb > REF_IMAGE_MAX_SIZE_MB:
                raise BytePlusException(
                    get_text("popup_ref_image_size_exceeded").format(
                        max_mb=REF_IMAGE_MAX_SIZE_MB, size_mb=f"{image_b64_size_mb:.3f}"
                    )
                )
            content_list.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_b64}"
                    },
                    "role": role,
                }
            )
            return len(image_b64.encode("utf-8"))
        return 0

    def _extract_image_hw(self, image):
        if image is None or not isinstance(image, torch.Tensor):
            raise BytePlusException(get_text("popup_ref_image_hw_out_of_range").format(
                min=IMAGE_MIN_EDGE, max=IMAGE_MAX_EDGE, width=0, height=0
            ))
        if image.ndim == 4:
            return int(image.shape[2]), int(image.shape[1])
        if image.ndim == 3:
            return int(image.shape[1]), int(image.shape[0])
        raise BytePlusException(get_text("popup_ref_image_hw_out_of_range").format(
            min=IMAGE_MIN_EDGE, max=IMAGE_MAX_EDGE, width=0, height=0
        ))

    def _validate_reference_image_constraints(self, image):
        if image is None:
            return
        width, height = self._extract_image_hw(image)
        if (
            width < IMAGE_MIN_EDGE
            or width > IMAGE_MAX_EDGE
            or height < IMAGE_MIN_EDGE
            or height > IMAGE_MAX_EDGE
        ):
            raise BytePlusException(
                get_text("popup_ref_image_hw_out_of_range").format(
                    min=IMAGE_MIN_EDGE, max=IMAGE_MAX_EDGE, width=width, height=height
                )
            )
        ratio = float(width) / float(height)
        if ratio < IMAGE_MIN_RATIO or ratio > IMAGE_MAX_RATIO:
            raise BytePlusException(
                get_text("popup_ref_image_ratio_out_of_range").format(
                    min=IMAGE_MIN_RATIO, max=IMAGE_MAX_RATIO, ratio=f"{ratio:.4f}"
                )
            )

    @staticmethod
    def _video_variant(video):
        """
        Trim window and crop of a VideoFromFile. Core's trim and crop nodes keep
        the same file, and the upload (save_to) applies them, so they must be
        part of the cache key or a trimmed clip would reuse the full clip's link.
        """
        parts = []
        window = getattr(video, "get_active_trim_window", None)
        if callable(window):
            try:
                parts.append("trim=" + ",".join(f"{float(x):.3f}" for x in window()))
            except Exception:
                return None  # unknown variant: do not cache
        crop = getattr(video, "_VideoFromFile__crop", None)
        if crop is not None:
            parts.append(f"crop={tuple(crop)}")
        return "".join(f"|{part}" for part in parts)

    def _build_comfy_video_upload_cache_key(self, video):
        try:
            stream_source = video.get_stream_source()
        except Exception:
            return None
        variant = self._video_variant(video)
        if variant is None:
            return None

        if isinstance(stream_source, str):
            path = os.path.abspath(stream_source)
            if not os.path.exists(path):
                return None
            st = os.stat(path)
            return f"path:{path}|{int(st.st_size)}|{int(st.st_mtime_ns)}{variant}"

        def _hash_buffer(size, reader):
            hasher = hashlib.sha256()
            sample_size = min(size, 1024 * 1024)
            head = reader(0, sample_size)
            hasher.update(head)
            if size > sample_size:
                tail = reader(size - sample_size, sample_size)
                hasher.update(tail)
            hasher.update(str(int(size)).encode("utf-8"))
            return hasher.hexdigest()

        if hasattr(stream_source, "getbuffer"):
            buffer_view = stream_source.getbuffer()
            size = int(buffer_view.nbytes)
            digest = _hash_buffer(
                size,
                lambda start, length: bytes(buffer_view[start : start + length]),
            )
            return f"buffer:{size}|{digest}{variant}"

        if hasattr(stream_source, "getvalue"):
            raw = stream_source.getvalue()
            size = len(raw)
            digest = _hash_buffer(size, lambda start, length: raw[start : start + length])
            return f"bytes:{size}|{digest}{variant}"

        return None

    def _prune_comfy_video_upload_cache(self):
        now_ts = time.time()
        expired_keys = [
            key
            for key, entry in COMFY_VIDEO_UPLOAD_CACHE.items()
            if not isinstance(entry, dict)
            or not entry.get("url")
            or float(entry.get("expire_at", 0.0) or 0.0) <= now_ts
        ]
        for key in expired_keys:
            COMFY_VIDEO_UPLOAD_CACHE.pop(key, None)

        if len(COMFY_VIDEO_UPLOAD_CACHE) <= COMFY_VIDEO_UPLOAD_CACHE_MAX_ENTRIES:
            return

        sorted_items = sorted(
            COMFY_VIDEO_UPLOAD_CACHE.items(),
            key=lambda kv: float(kv[1].get("saved_at", 0.0) or 0.0),
        )
        remove_count = len(COMFY_VIDEO_UPLOAD_CACHE) - COMFY_VIDEO_UPLOAD_CACHE_MAX_ENTRIES
        for key, _ in sorted_items[:remove_count]:
            COMFY_VIDEO_UPLOAD_CACHE.pop(key, None)

    def _get_cached_comfy_video_url(self, cache_key):
        if not cache_key:
            return None
        self._prune_comfy_video_upload_cache()
        entry = COMFY_VIDEO_UPLOAD_CACHE.get(cache_key)
        if not isinstance(entry, dict):
            return None
        return (entry.get("url") or "").strip() or None

    def _save_cached_comfy_video_url(self, cache_key, video_url):
        if not cache_key:
            return
        normalized_url = (video_url or "").strip()
        if not normalized_url:
            return
        now_ts = time.time()
        COMFY_VIDEO_UPLOAD_CACHE[cache_key] = {
            "url": normalized_url,
            "saved_at": now_ts,
            "expire_at": now_ts + COMFY_VIDEO_UPLOAD_CACHE_TTL_SECONDS,
        }
        self._prune_comfy_video_upload_cache()

    def _append_media_url_content(self, content_list, media_url, media_type, role):
        normalized_url = (media_url or "").strip()
        if not normalized_url:
            return
        content_list.append(
            {
                "type": media_type,
                media_type: {"url": normalized_url},
                "role": role,
            }
        )

    def _audio_to_data_uri(self, audio, max_duration=REF_AUDIO_MAX_DURATION):
        if audio is None:
            return None

        try:
            waveform, sample_rate = audio_waveform(audio)
        except BytePlusException:
            raise BytePlusException(get_text("popup_audio_invalid"))

        duration = float(waveform.shape[1]) / float(sample_rate)
        if duration < REF_AUDIO_MIN_DURATION or duration > max_duration:
            raise BytePlusException(
                get_text("popup_ref_audio_duration_out_of_range").format(
                    min=REF_AUDIO_MIN_DURATION,
                    max=max_duration,
                    duration=f"{duration:.3f}",
                )
            )

        base64_audio = base64.b64encode(audio_to_wav_bytes(audio)).decode("utf-8")
        size_mb = float(len(base64_audio.encode("utf-8"))) / (1024.0 * 1024.0)
        if size_mb > REF_AUDIO_MAX_SIZE_MB:
            raise BytePlusException(
                get_text("popup_ref_audio_size_exceeded").format(
                    max_mb=REF_AUDIO_MAX_SIZE_MB, size_mb=f"{size_mb:.3f}"
                )
            )

        data_uri = f"data:audio/wav;base64,{base64_audio}"
        return data_uri, duration, len(data_uri.encode("utf-8"))

    def _append_audio_content(
        self, content_list, audio, role, max_duration=REF_AUDIO_MAX_DURATION
    ):
        audio_data = self._audio_to_data_uri(audio, max_duration=max_duration)
        if audio_data is None:
            return None
        audio_data_uri, duration, request_bytes = audio_data
        content_list.append(
            {
                "type": "audio_url",
                "audio_url": {"url": audio_data_uri},
                "role": role,
            }
        )
        return duration, request_bytes

    @staticmethod
    def _pending_outputs(state):
        """
        A non_blocking run without videos yet: the task IDs in the response. The
        video outputs get ExecutionBlockers (a list output cannot be None), so
        nodes using them are skipped.
        """
        return comfy_io.NodeOutput(
            ExecutionBlocker(None), ExecutionBlocker(None), json.dumps(state, ensure_ascii=False, indent=2)
        )

    @staticmethod
    def _ignored_failure_outputs(runner):
        """
        Every task failed in a workflow with several such nodes: the outputs are
        blocked with the failure, so ComfyUI reports it at the nodes using them
        and the other branches still run (no placeholder gets saved as a result).
        """
        message = runner.ignored_failure or get_text("err_batch_fail_all")
        return comfy_io.NodeOutput(
            ExecutionBlocker(message), ExecutionBlocker(message), json.dumps({"error": message})
        )

    async def _handle_batch_success_async(
        self,
        successful_tasks,
        generation_count,
        session,
    ):
        """
        Download the videos and last frames of succeeded tasks and build the
        outputs: every video (for the list VIDEO output) and a batch of their last
        frames in the same order. Nothing is written to the output folder.
        """
        if generation_count > 1:
            log_msg("batch_handling", count=len(successful_tasks))

        temp_save_path = "BytePlus"
        video_prefix = "BytePlus_Vid_Temp"
        frame_prefix = "BytePlus_Frame_Temp"

        async def _process_task(task):
            video_url = task.content.video_url
            last_frame_url = getattr(task.content, "last_frame_url", None)
            seed = getattr(task, "seed", random.randint(0, VIDEO_MAX_SEED))

            v_coro = download_video_to_temp(
                session, video_url, video_prefix, seed, temp_save_path
            )

            f_coro = None
            if last_frame_url:
                f_coro = download_image_to_temp(
                    session, last_frame_url, frame_prefix, seed, temp_save_path
                )

            if f_coro:
                v_path, (f_tensor, f_path) = await asyncio.gather(v_coro, f_coro)
            else:
                v_path = await v_coro
                f_tensor, f_path = None, None

            resp = task.model_dump()
            for k in ["created_at", "updated_at"]:
                if k in resp and isinstance(resp[k], (int, float)):
                    resp[k] = datetime.datetime.fromtimestamp(resp[k]).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )

            return {
                "task_id": getattr(task, "id", None),
                "seed": seed,
                "video_path": v_path,
                "frame_tensor": f_tensor,
                "frame_path": f_path,
                "response": resp,
            }

        # Interruptible: a large download must not hold up Stop.
        results = await wait_interruptible(
            asyncio.gather(*[_process_task(t) for t in successful_tasks], return_exceptions=True)
        )
        valid_results = []
        missing = []  # paid tasks whose video could not be downloaded
        for task, res in zip(successful_tasks, results):
            if isinstance(res, comfy.model_management.InterruptProcessingException):
                raise res
            if isinstance(res, Exception):
                log_msg("err_download_url", url="batch_task", e=res)
                missing.append(str(getattr(task, "id", "?")))
                continue
            if not res["video_path"]:
                missing.append(str(res["task_id"] or "?"))
            valid_results.append(res)
        download_error = None
        if missing and len(missing) == len(successful_tasks):
            # Block the outputs with the reason (ComfyUI reports it at the nodes that use
            # them); the response output still carries the task IDs and video links.
            download_error = get_text("err_video_download_failed", task_ids=", ".join(missing))
            log_msg("err_video_download_failed", task_ids=", ".join(missing))
        elif missing:
            log_msg(
                "batch_video_download_partial",
                done=len(successful_tasks) - len(missing),
                total=len(successful_tasks),
                task_ids=", ".join(missing),
            )

        valid_results.sort(key=lambda x: x["seed"])

        all_responses = []
        # One entry per downloaded video, so videos[i] and frames[i] are the same task.
        videos = []
        frames = []

        for res in valid_results:
            all_responses.append(res["response"])
            v_path = res["video_path"]
            if not v_path:
                continue
            f_tensor = res["frame_tensor"]
            if f_tensor is None:
                f_tensor = extract_last_frame_tensor(v_path)
            videos.append(VideoFromFile(v_path))
            frames.append(f_tensor)

        response = json.dumps(all_responses, indent=2)

        if download_error:
            return comfy_io.NodeOutput(ExecutionBlocker(download_error), ExecutionBlocker(download_error), response)
        if not videos:
            return comfy_io.NodeOutput(ExecutionBlocker(None), ExecutionBlocker(None), response)
        found = [frame for frame in frames if frame is not None]
        if len(found) < len(frames):
            log_msg("batch_last_frame_missing", missing=len(frames) - len(found), total=len(frames))
        return comfy_io.NodeOutput(
            videos, safe_cat_tensors(found) if found else ExecutionBlocker(None), response
        )

    async def _run_prebuilt_content(
        self,
        client,
        node_id,
        model_name,
        content,
        estimation_duration,
        resolution,
        generation_count,
        non_blocking,
        extra_api_params,
        service_tier=None,
        execution_expires_after=None,
        ignore_errors=False,
    ):
        """
        Submit tasks whose content is already built (e.g. a draft_task
        reference for a final video) and collect the results.
        """
        runner = BytePlusGenerationExecutor(client, node_id, ignore_errors=ignore_errors)
        successful_tasks = await runner.run_batch_tasks(
            model_name=model_name,
            content=content,
            estimation_duration=estimation_duration,
            resolution=resolution,
            generation_count=generation_count,
            non_blocking=non_blocking,
            non_blocking_cache_dict=self.NON_BLOCKING_TASK_CACHE,
            service_tier=service_tier,
            execution_expires_after=execution_expires_after,
            extra_api_params=extra_api_params,
            return_last_frame=True,
        )

        if isinstance(successful_tasks, dict) and successful_tasks.get("non_blocking"):
            return self._pending_outputs(successful_tasks)

        if not successful_tasks and ignore_errors:
            return self._ignored_failure_outputs(runner)

        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(force_close=True)
        ) as session:
            ret_results = await self._handle_batch_success_async(successful_tasks, generation_count, session)
            await asyncio.sleep(0.25)
        return ret_results

    async def _common_generation_logic(
        self,
        client,
        prompt,
        duration,
        resolution,
        aspect_ratio,
        seed,
        generation_count,
        non_blocking,
        node_id,
        model_name,
        content,
        forbidden_params,
        service_tier="default",
        execution_expires_after=None,
        is_auto_duration=False,
        extra_api_params=None,
        return_last_frame=True,
        on_tasks_created=None,
        node_class_type=None,
        workflow_prompt=None,
    ):
        """
        Shared video generation flow: build parameters, submit, poll and collect results.
        """
        try:
            _raise_if_text_params(prompt, forbidden_params)

            if extra_api_params is None:
                extra_api_params = {}

            if resolution is not None:
                extra_api_params["resolution"] = resolution
            if aspect_ratio is not None:
                extra_api_params["ratio"] = aspect_ratio
            if seed is not None:
                extra_api_params["seed"] = seed

            estimation_duration = 5
            if is_auto_duration:
                extra_api_params["duration"] = -1
            else:
                key, val, est = _calculate_duration_and_frames_args(duration)
                extra_api_params[key] = val
                estimation_duration = est

            if prompt:
                content.insert(0, {"type": "text", "text": prompt})
            comfy.model_management.throw_exception_if_processing_interrupted()

            ignore_errors = False
            if node_class_type:
                node_count = get_node_count_in_workflow(node_class_type, prompt=workflow_prompt)
                # log_msg("debug_node_count", count=node_count, type=node_class_type)
                ignore_errors = node_count > 1

            runner = BytePlusGenerationExecutor(client, node_id, ignore_errors=ignore_errors)
            successful_tasks = await runner.run_batch_tasks(
                model_name=model_name,
                content=content,
                estimation_duration=estimation_duration,
                resolution=resolution,
                generation_count=generation_count,
                non_blocking=non_blocking,
                non_blocking_cache_dict=self.NON_BLOCKING_TASK_CACHE,
                service_tier=service_tier,
                execution_expires_after=execution_expires_after,
                extra_api_params=extra_api_params,
                return_last_frame=return_last_frame,
                on_tasks_created=on_tasks_created,
            )

            if isinstance(successful_tasks, dict) and successful_tasks.get(
                "non_blocking"
            ):
                return self._pending_outputs(successful_tasks)

            if not successful_tasks and ignore_errors:
                return self._ignored_failure_outputs(runner)

            async with aiohttp.ClientSession() as session:
                ret_results = await self._handle_batch_success_async(successful_tasks, generation_count, session)
                await asyncio.sleep(0.25)
            return ret_results

        except Exception as e:
            if isinstance(e, comfy.model_management.InterruptProcessingException):
                raise e
            s_e = str(e)
            if s_e.startswith("[BytePlus]"):
                raise e
            raise BytePlusException(format_api_error(e))


def build_seedance1_frame_content(helper, first_frame, last_frame):
    """
    Seedance 1.x content items for the first/last frames (base64 data URIs with
    roles first_frame / last_frame), checked against the request size limits.
    """
    content = []
    total_image_request_bytes = helper._append_image_content(content, first_frame, "first_frame")

    if last_frame is not None:
        if first_frame is None:
            raise BytePlusException(get_text("popup_first_frame_missing"))
        total_image_request_bytes += helper._append_image_content(content, last_frame, "last_frame")

    total_image_request_mb = float(total_image_request_bytes) / (1024.0 * 1024.0)
    if total_image_request_mb > REF_IMAGE_MAX_TOTAL_REQUEST_MB:
        raise BytePlusException(
            get_text("popup_ref_image_total_size_exceeded").format(
                max_mb=REF_IMAGE_MAX_TOTAL_REQUEST_MB, size_mb=f"{total_image_request_mb:.3f}"
            )
        )
    return content


class BytePlusProgressTest(comfy_io.ComfyNode):
    """
    Dev-only progress bar test node.
    Simulates progress events locally (no generation) to debug the frontend.
    """
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        test_model_options = (
            ["None"] + VIDEO_1_UI_OPTIONS + VIDEO_2_UI_OPTIONS
        )

        return comfy_io.Schema(
            node_id="BytePlusProgressTest",
            display_name="BytePlus Progress Test",
            category=GLOBAL_CATEGORY,
            is_output_node=True,
            is_dev_only=True,
            inputs=[
                comfy_io.Int.Input("duration_seconds", default=10, min=1, max=300),
                comfy_io.Int.Input("steps", default=20, min=1, max=600),
                comfy_io.Combo.Input(
                    "test_model",
                    options=test_model_options,
                    default="None",
                ),
                comfy_io.Combo.Input(
                    "test_resolution",
                    options=["720p", "1080p", "4k", "480p"],
                    default="720p",
                ),
            ],
            hidden=[comfy_io.Hidden.unique_id],
            outputs=[
                comfy_io.String.Output(display_name="response"),
            ],
        )

    @classmethod
    async def execute(
        cls,
        duration_seconds,
        steps,
        test_model="None",
        test_resolution="720p",
    ) -> comfy_io.NodeOutput:
        node_id = cls.hidden.unique_id
        ps_instance = PromptServer.instance

        client = None
        if test_model != "None":
            try:
                client = get_client()
            except BytePlusException:
                client = None  # no saved key: simulate without the task-history estimate

        if client and test_model != "None":
            real_model_id = VIDEO_MODEL_MAP.get(test_model, test_model)

            est_time, method = await _get_api_estimated_time_async(
                client.ark, real_model_id, duration_seconds, test_resolution
            )

            history_data = []
            try:
                resp = await asyncio.to_thread(
                    client.ark.content_generation.tasks.list,
                    status="succeeded",
                    model=real_model_id,
                    page_size=HISTORY_PAGE_SIZE,
                )
                if resp.items:
                    for item in resp.items:
                        if not (
                            hasattr(item, "resolution")
                            and item.resolution == test_resolution
                        ):
                            continue

                        item_dur = getattr(item, "duration", 0)

                        t_start = item.created_at
                        t_end = item.updated_at
                        if hasattr(t_start, "timestamp"):
                            t_start = t_start.timestamp()
                        if hasattr(t_end, "timestamp"):
                            t_end = t_end.timestamp()

                        raw_diff = float(t_end) - float(t_start)
                        try:
                            local_offset = (
                                datetime.datetime.now()
                                .astimezone()
                                .utcoffset()
                                .total_seconds()
                            )
                        except Exception:
                            local_offset = 0

                        fixed_diff = raw_diff - local_offset
                        task_time = (
                            fixed_diff
                            if fixed_diff > 0 and abs(fixed_diff) < abs(raw_diff)
                            else raw_diff
                        )

                        history_data.append(
                            {
                                "task_id": item.id,
                                "req_duration": item_dur,
                                "actual_time": float(f"{task_time:.2f}"),
                                "raw_diff": float(f"{raw_diff:.2f}"),
                            }
                        )
            except Exception as e:
                history_data.append({"error": str(e)})

            result = {
                "estimated_time": est_time,
                "estimation_method": method,
                "history_samples_count": len(history_data),
                "history_samples_top20": history_data[:20],
            }

            return comfy_io.NodeOutput(json.dumps(result, indent=2))

        total_seconds = max(1, int(duration_seconds))
        total_steps = max(1, int(steps))
        step_interval = float(total_seconds) / float(total_steps)

        elapsed = 0.0
        try:
            for i in range(total_steps + 1):
                comfy.model_management.throw_exception_if_processing_interrupted()
                _update_node_progress(
                    node_id, int(elapsed), int(total_seconds), ps_instance
                )

                if i < total_steps:
                    await asyncio.sleep(step_interval)
                    elapsed += step_interval
        finally:
            _finish_node_progress(node_id, ps_instance)

        return comfy_io.NodeOutput("BytePlus Progress Test Finished")


class BytePlusVideoQueryTasks(comfy_io.ComfyNode):
    """
    Lists video generation tasks and their status.
    """
    MODELS = QUERY_TASKS_MODEL_LIST
    STATUSES = [
        "all",
        "succeeded",
        "failed",
        "running",
        "queued",
        "cancelled",
        "expired",
    ]

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusVideoQueryTasks",
            display_name="BytePlus Video Query Tasks",
            category=GLOBAL_CATEGORY,
            is_output_node=True,
            inputs=[
                comfy_io.Int.Input("page_num", default=1),
                comfy_io.Int.Input("page_size", default=10),
                comfy_io.Combo.Input("status", options=cls.STATUSES, default="all"),
                comfy_io.Combo.Input(
                    "service_tier", options=["default", "flex"], default="default"
                ),
                comfy_io.String.Input("task_ids", default=""),
                comfy_io.Combo.Input(
                    "model_version", options=cls.MODELS, default="all"
                ),
                comfy_io.Int.Input("seed", default=0, min=0, max=VIDEO_MAX_SEED),
            ],
            outputs=[
                comfy_io.String.Output(display_name="task_list_json"),
                comfy_io.Int.Output(display_name="total_tasks"),
            ],
        )

    @classmethod
    @with_client("client", get_client)
    async def execute(
        cls,
        client,
        page_num,
        page_size,
        status,
        service_tier,
        task_ids,
        model_version,
        seed,
    ) -> comfy_io.NodeOutput:
        ark_client = client.ark
        base_kwargs = {"page_num": page_num, "page_size": page_size}

        if status != "all":
            base_kwargs["status"] = status

        if service_tier:
            base_kwargs["service_tier"] = service_tier

        if task_ids and task_ids.strip():
            base_kwargs["task_ids"] = [
                tid.strip() for tid in task_ids.split("\n") if tid.strip()
            ]

        target_models = resolve_query_models(model_version)

        try:
            tasks = []
            for mid in target_models:
                kw = base_kwargs.copy()
                if mid is not None:
                    kw["model"] = mid
                tasks.append(
                    asyncio.to_thread(ark_client.content_generation.tasks.list, **kw)
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)
            all_items = []
            total_count = 0

            for res in results:
                if isinstance(res, Exception):
                    print(f"[BytePlus] Query Partial Error: {res}")
                    continue
                total_count += getattr(res, "total", 0)
                if hasattr(res, "items") and res.items:
                    for item in res.items:
                        item_dict = item.model_dump()
                        if "created_at" in item_dict and isinstance(
                            item_dict["created_at"], (int, float)
                        ):
                            item_dict["created_at_ts"] = item_dict["created_at"]
                            item_dict["created_at"] = datetime.datetime.fromtimestamp(
                                item_dict["created_at"]
                            ).strftime("%Y-%m-%d %H:%M:%S")
                        if "updated_at" in item_dict and isinstance(
                            item_dict["updated_at"], (int, float)
                        ):
                            item_dict["updated_at"] = datetime.datetime.fromtimestamp(
                                item_dict["updated_at"]
                            ).strftime("%Y-%m-%d %H:%M:%S")
                        all_items.append(item_dict)

            if not all_items and any(isinstance(r, Exception) for r in results):
                first_err = next(r for r in results if isinstance(r, Exception))
                return comfy_io.NodeOutput(
                    json.dumps(
                        {"error": format_api_error(first_err)}, ensure_ascii=False
                    ),
                    0,
                )

            all_items.sort(key=lambda x: x.get("created_at_ts", 0), reverse=True)
            for item in all_items:
                if "created_at_ts" in item:
                    del item["created_at_ts"]

            if len(target_models) > 1:
                all_items = all_items[:page_size]

            return comfy_io.NodeOutput(
                json.dumps(all_items, indent=2, ensure_ascii=False), total_count
            )
        except Exception as e:
            return comfy_io.NodeOutput(
                json.dumps({"error": format_api_error(e)}, ensure_ascii=False), 0
            )
