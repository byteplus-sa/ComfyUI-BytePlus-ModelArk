import os
import time
import random
import datetime
import asyncio
import aiohttp
import json
import math
import logging
import base64
import io
import hashlib
from urllib.parse import urlparse

import folder_paths
import comfy.model_management
from server import PromptServer
import torch
import PIL.Image

from comfy_api.latest import io as comfy_io
from comfy_api.input_impl import VideoFromFile

from .audio_utils import audio_to_wav_bytes, audio_waveform
from .nodes_shared import (
    GLOBAL_CATEGORY,
    _image_to_base64,
    log_msg,
    get_text,
    format_api_error,
    BytePlusClientType,
    BytePlusException,
    get_node_count_in_workflow,
    create_white_image_tensor,
    create_white_video,
    probe_video_file,
    extract_last_frame_tensor,
)
from .nodes_video_schema import (
    get_common_video_seed_inputs,
    get_common_video_runtime_inputs,
    get_duration_input,
    get_resolution_input,
    get_seedance2_resolutions,
    get_aspect_ratio_input,
    _calculate_duration_and_frames_args,
    ASPECT_RATIOS,
    resolve_model_id,
    resolve_query_models,
    VIDEO_1_UI_OPTIONS,
    VIDEO_1_5_UI_OPTIONS,
    VIDEO_2_UI_OPTIONS,
    QUERY_TASKS_MODEL_LIST,
)
from .utils_download import (
    download_video_to_temp,
    download_image_to_temp,
    save_to_output,
)

from .executor import (
    BytePlusGenerationExecutor,
    _get_api_estimated_time_async,
    _update_node_progress,
    _finish_node_progress,
    HISTORY_PAGE_SIZE,
)
from .models_config import (
    VIDEO_MODEL_MAP,
    VIDEO_2_MODEL_RESOLUTIONS,
    VIDEO_2_MODEL_MAX_DURATIONS,
    VIDEO_2_MODEL_REFERENCE_LIMITS,
    SEEDANCE_2_5_FAMILY,
    SEEDANCE_2_5_TASK_TYPES,
    SEEDANCE_2_5_OUTPUT_FORMATS,
    SEEDANCE_DRAFT_RESOLUTION,
    SEEDANCE_DRAFT_FINAL_RESOLUTIONS,
)

logging.getLogger("byteplussdkarkruntime").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)


NON_BLOCKING_TASK_CACHE = {}
LAST_SEEDANCE_1_5_DRAFT_TASK_ID = {}
# node_id -> {"model": model_version, "ids": [draft task ids]}
LAST_SEEDANCE_2_DRAFT_TASKS = {}
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


def _parse_video_urls(text) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _parse_reference_urls(text) -> list[str]:
    """https:// URLs or asset://<asset_id> references, one per line."""
    urls = _parse_video_urls(text)
    for url in urls:
        lowered = url.lower()
        if not (lowered.startswith("https://") or (_is_asset_uri(url) and len(url) > len("asset://"))):
            raise BytePlusException(get_text("err_asset_uri_invalid", value=url))
    return urls


def _is_asset_uri(url) -> bool:
    return str(url or "").strip().lower().startswith("asset://")


def _parse_draft_task_ids(text) -> list[str]:
    return [line.strip() for line in str(text or "").replace(",", "\n").splitlines() if line.strip()]


def select_draft_task_ids(draft_mode, reuse_last_draft_task, draft_task_id, cached_ids):
    """
    Draft task IDs to render as final videos, or [] to generate normally.

    Mirrors what the UI shows: draft_task_id is only visible (and only used)
    in draft mode with reuse off, so a hidden leftover ID never hijacks a run.
    Reuse without a remembered draft raises instead of silently billing a new
    draft.
    """
    if not draft_mode:
        return []
    if reuse_last_draft_task:
        if not cached_ids:
            raise BytePlusException(get_text("err_no_draft_to_reuse"))
        return list(cached_ids)
    return _parse_draft_task_ids(draft_task_id)


def build_draft_final_content(draft_ids, generation_count):
    """Content and task count for final videos rendered from draft tasks."""
    drafts = [[{"type": "draft_task", "draft_task": {"id": tid}}] for tid in draft_ids]
    if len(drafts) == 1:
        return drafts[0], generation_count
    return drafts, len(drafts)


def validate_seedance25_task_type(task_type, has_reference_video, aspect_ratio, auto_duration):
    """
    Check Seedance 2.5 omni-reference task constraints before submitting, so
    they fail fast instead of as an asynchronous task error.
    """
    if task_type in ("edit", "extend") and not has_reference_video:
        raise BytePlusException(
            get_text("err_seedance25_task_type_needs_video", task_type=task_type)
        )
    if task_type == "edit" and (aspect_ratio != "adaptive" or not auto_duration):
        raise BytePlusException(get_text("err_seedance25_editing_params"))
    if task_type == "extend" and aspect_ratio != "adaptive":
        raise BytePlusException(get_text("err_seedance25_extend_params"))
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


def _create_named_autogrow_input(name, input_template, names, min_slots):
    return comfy_io.Autogrow.Input(
        name,
        template=comfy_io.Autogrow.TemplateNames(
            input=input_template,
            names=names,
            min=min_slots,
        ),
    )


def _collect_dynamic_inputs(values=None, kwargs=None, prefix=None):
    collected = []

    if isinstance(values, dict):
        sorted_items = sorted(values.items(), key=lambda item: _get_dynamic_input_order(item[0]))
        collected.extend([value for _, value in sorted_items])
    elif values is not None:
        collected.append(values)

    if kwargs and prefix:
        sorted_keys = sorted([key for key in kwargs.keys() if key.startswith(prefix)], key=_get_dynamic_input_order)
        collected.extend([kwargs[key] for key in sorted_keys])

    return [value for value in collected if value is not None]


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


def validate_seedance2_reference_counts(model_version, images, videos, audios):
    limits = VIDEO_2_MODEL_REFERENCE_LIMITS.get(model_version)
    if limits is None:
        raise BytePlusException(
            get_text("err_model_not_supported").format(model=model_version)
        )
    counts = {
        "images": len(images),
        "videos": len(videos),
        "audios": len(audios),
    }
    for kind, count in counts.items():
        if count > limits[kind]:
            raise BytePlusException(
                get_text("popup_ref_count_exceeded").format(
                    model=model_version,
                    max=limits[kind],
                    kind=get_text(f"ref_kind_{kind}"),
                    count=count,
                )
            )
    return counts


from .constants import (
    VIDEO_MAX_SEED,
    VIDEO_DEFAULT_TIMEOUT,
    IMAGE_MIN_EDGE,
    IMAGE_MAX_EDGE,
    IMAGE_MIN_RATIO,
    IMAGE_MAX_RATIO,
    REF_IMAGE_MAX_SIZE_MB,
    REF_IMAGE_MAX_TOTAL_REQUEST_MB,
    REF_VIDEO_MIN_DURATION,
    REF_VIDEO_MAX_DURATION,
    REF_VIDEO_MAX_TOTAL_DURATION,
    REF_VIDEO_MAX_SIZE_MB,
    REF_VIDEO_MIN_PIXELS,
    REF_VIDEO_MAX_PIXELS,
    REF_VIDEO_MIN_FPS,
    REF_VIDEO_MAX_FPS,
    REF_AUDIO_MIN_DURATION,
    REF_AUDIO_MAX_DURATION,
    REF_AUDIO_MAX_TOTAL_DURATION,
    REF_AUDIO_MAX_SIZE_MB,
    REF_AUDIO_MAX_TOTAL_REQUEST_MB,
    REF_MEDIA_MAX_DURATION_SEEDANCE_2_5,
    DEFAULT_FILENAME_PREFIX,
    VIDEO_FRAME_RATE,
    VIDEO_RESOLUTION_PIXELS,
)

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

    def _validate_reference_video_url_format(self, video_url):
        normalized_url = (video_url or "").strip()
        if not normalized_url:
            return
        path = urlparse(normalized_url).path.lower()
        if not (path.endswith(".mp4") or path.endswith(".mov")):
            raise BytePlusException(get_text("popup_ref_video_url_format"))

    def _get_video_stream_size_bytes(self, stream_source):
        if isinstance(stream_source, str):
            return os.path.getsize(stream_source)
        if hasattr(stream_source, "getbuffer"):
            return int(stream_source.getbuffer().nbytes)
        if hasattr(stream_source, "getvalue"):
            return len(stream_source.getvalue())
        return 0

    def _build_comfy_video_upload_cache_key(self, video):
        try:
            stream_source = video.get_stream_source()
        except Exception:
            return None

        if isinstance(stream_source, str):
            path = os.path.abspath(stream_source)
            if not os.path.exists(path):
                return None
            st = os.stat(path)
            return f"path:{path}|{int(st.st_size)}|{int(st.st_mtime_ns)}"

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
            return f"buffer:{size}|{digest}"

        if hasattr(stream_source, "getvalue"):
            raw = stream_source.getvalue()
            size = len(raw)
            digest = _hash_buffer(size, lambda start, length: raw[start : start + length])
            return f"bytes:{size}|{digest}"

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

    def _get_video_duration_seconds(self, video, stream_source):
        duration_fallback = None
        try:
            duration_fallback = float(video.get_duration())
        except Exception:
            duration_fallback = None

        def _safe_numeric_from_video(method_name):
            getter = getattr(video, method_name, None)
            if not callable(getter):
                return None
            try:
                val = float(getter())
                if val > 0:
                    return val
            except Exception:
                return None
            return None

        fps = _safe_numeric_from_video("get_fps") or _safe_numeric_from_video("get_frame_rate")
        frame_count = _safe_numeric_from_video("get_frame_count")
        if fps and frame_count:
            return frame_count / fps

        if isinstance(stream_source, str) and os.path.exists(stream_source):
            probed = probe_video_file(stream_source)
            if probed.get("fps") and probed.get("frame_count"):
                return probed["frame_count"] / probed["fps"]
            if probed.get("duration"):
                return probed["duration"]

        if duration_fallback is not None and duration_fallback > 0:
            return duration_fallback

        raise BytePlusException(get_text("popup_ref_video_invalid"))

    @staticmethod
    def _read_video_metadata_value(video, method_names):
        for method_name in method_names:
            getter = getattr(video, method_name, None)
            if not callable(getter):
                continue
            try:
                value = getter()
            except Exception:
                continue
            if value not in (None, "", 0, 0.0):
                return value
        return None

    def _get_reference_video_metadata(self, video, stream_source):
        fps = self._read_video_metadata_value(
            video, ("get_frame_rate", "get_fps", "get_framerate")
        )
        video_codec = self._read_video_metadata_value(
            video, ("get_video_codec", "get_codec", "get_codec_name")
        )
        audio_codec = self._read_video_metadata_value(
            video, ("get_audio_codec", "get_audio_codec_name")
        )

        if isinstance(stream_source, str) and os.path.exists(stream_source):
            probed = probe_video_file(stream_source)
            if fps is None:
                fps = probed.get("fps")
            if video_codec is None:
                video_codec = probed.get("video_codec")
            if audio_codec is None:
                audio_codec = probed.get("audio_codec")

        try:
            fps = float(fps) if fps is not None else None
        except (TypeError, ValueError):
            fps = None
        return fps, str(video_codec or "").lower(), str(audio_codec or "").lower()

    @staticmethod
    def _codec_matches(codec, allowed_tokens):
        normalized = str(codec or "").lower().replace(".", "").replace("-", "")
        return not normalized or any(token in normalized for token in allowed_tokens)

    def _validate_single_reference_video(self, video, max_duration=REF_VIDEO_MAX_DURATION):
        try:
            container_format = str(video.get_container_format() or "").lower()
            width, height = video.get_dimensions()
            stream_source = video.get_stream_source()
            duration = self._get_video_duration_seconds(video, stream_source)
            size_bytes = self._get_video_stream_size_bytes(stream_source)
            fps, video_codec, audio_codec = self._get_reference_video_metadata(
                video, stream_source
            )
        except Exception as e:
            raise BytePlusException(get_text("popup_ref_video_invalid").format(msg=str(e)))

        if ("mp4" not in container_format) and ("mov" not in container_format):
            raise BytePlusException(
                get_text("popup_ref_video_format").format(fmt=container_format or "unknown")
            )

        if fps is not None and not (REF_VIDEO_MIN_FPS <= fps <= REF_VIDEO_MAX_FPS):
            raise BytePlusException(
                get_text("popup_ref_video_fps_out_of_range").format(
                    min=REF_VIDEO_MIN_FPS, max=REF_VIDEO_MAX_FPS, fps=f"{fps:.3f}"
                )
            )
        if not self._codec_matches(
            video_codec, ("h264", "avc1", "avc", "h265", "hevc")
        ):
            raise BytePlusException(
                get_text("popup_ref_video_codec_unsupported").format(codec=video_codec)
            )
        if not self._codec_matches(audio_codec, ("aac", "mp3", "mpeg3")):
            raise BytePlusException(
                get_text("popup_ref_audio_codec_unsupported").format(codec=audio_codec)
            )

        if (
            width < IMAGE_MIN_EDGE
            or width > IMAGE_MAX_EDGE
            or height < IMAGE_MIN_EDGE
            or height > IMAGE_MAX_EDGE
        ):
            raise BytePlusException(
                get_text("popup_ref_video_hw_out_of_range").format(
                    min=IMAGE_MIN_EDGE, max=IMAGE_MAX_EDGE, width=width, height=height
                )
            )

        ratio = float(width) / float(height)
        if ratio < IMAGE_MIN_RATIO or ratio > IMAGE_MAX_RATIO:
            raise BytePlusException(
                get_text("popup_ref_video_ratio_out_of_range").format(
                    min=IMAGE_MIN_RATIO, max=IMAGE_MAX_RATIO, ratio=f"{ratio:.4f}"
                )
            )

        pixels = int(width) * int(height)
        if pixels < REF_VIDEO_MIN_PIXELS or pixels > REF_VIDEO_MAX_PIXELS:
            raise BytePlusException(
                get_text("popup_ref_video_pixels_out_of_range").format(
                    min=REF_VIDEO_MIN_PIXELS, max=REF_VIDEO_MAX_PIXELS, pixels=pixels
                )
            )

        if duration < REF_VIDEO_MIN_DURATION or duration > max_duration:
            raise BytePlusException(
                get_text("popup_ref_video_duration_out_of_range").format(
                    min=REF_VIDEO_MIN_DURATION,
                    max=max_duration,
                    duration=f"{duration:.3f}",
                )
            )

        size_mb = float(size_bytes) / (1024.0 * 1024.0)
        if size_mb > REF_VIDEO_MAX_SIZE_MB:
            raise BytePlusException(
                get_text("popup_ref_video_size_exceeded").format(
                    max_mb=REF_VIDEO_MAX_SIZE_MB, size_mb=f"{size_mb:.3f}"
                )
            )

        return duration

    def _validate_reference_videos_constraints(
        self,
        ref_videos,
        ref_video_urls=None,
        max_duration=REF_VIDEO_MAX_DURATION,
        max_total_duration=REF_VIDEO_MAX_TOTAL_DURATION,
    ):
        if ref_video_urls is None:
            ref_video_urls = []
        total_duration = 0.0
        for v in ref_videos:
            if v is None:
                continue
            total_duration += self._validate_single_reference_video(
                v, max_duration=max_duration
            )

        if total_duration > max_total_duration:
            raise BytePlusException(
                get_text("popup_ref_video_total_duration_exceeded").format(
                    max=max_total_duration, duration=f"{total_duration:.3f}"
                )
            )

        for video_url in ref_video_urls:
            self._validate_reference_video_url_format(video_url)

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

    async def _handle_batch_success_async(
        self,
        successful_tasks,
        filename_prefix,
        generation_count,
        save_last_frame_batch,
        session,
    ):
        """
        Download the videos and last frames of succeeded tasks and build the outputs.
        """
        # t_start = time.time()
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
                "seed": seed,
                "video_path": v_path,
                "frame_tensor": f_tensor,
                "frame_path": f_path,
                "response": resp,
            }

        results = await asyncio.gather(
            *[_process_task(t) for t in successful_tasks], return_exceptions=True
        )
        valid_results = []
        for res in results:
            if isinstance(res, Exception):
                log_msg("err_download_url", url="batch_task", e=res)
                continue
            valid_results.append(res)

        valid_results.sort(key=lambda x: x["seed"])

        all_responses = []
        first_video = None
        first_frame = None

        for res in valid_results:
            if res["frame_tensor"] is None and res["video_path"]:
                res["frame_tensor"] = extract_last_frame_tensor(res["video_path"])

            all_responses.append(res["response"])
            v_path = res["video_path"]
            f_tensor = res["frame_tensor"]
            f_path = res["frame_path"]

            if first_video is None and v_path:
                first_video = VideoFromFile(v_path)
            if first_frame is None and f_tensor is not None:
                first_frame = f_tensor

            if generation_count > 1:
                save_to_output(v_path, filename_prefix)
                if save_last_frame_batch and f_path:
                    save_to_output(f_path, filename_prefix)

        # t_end = time.time()
        # print(f"[BytePlus Debug] Batch handling finished in {t_end - t_start:.2f}s")
        
        return comfy_io.NodeOutput(
            first_video, first_frame, json.dumps(all_responses, indent=2)
        )

    @staticmethod
    def _estimate_video_tokens(model_name, resolution, duration, has_audio=False, is_draft=False):
        from .quota import QuotaManager

        return QuotaManager.instance().estimate_video_tokens(
            model_name,
            width=1,
            height=VIDEO_RESOLUTION_PIXELS.get(resolution, 1280 * 720),
            duration=duration,
            fps=VIDEO_FRAME_RATE,
            has_audio=has_audio,
            is_draft=is_draft,
        )

    @staticmethod
    def _record_usage(client, model_name, ret_results):
        """Add the completion tokens reported by finished tasks to the quota."""
        if not ret_results or not ret_results[2]:
            return
        try:
            total_tokens = 0
            for item in json.loads(ret_results[2]):
                if isinstance(item, dict) and item.get("usage") and "completion_tokens" in item["usage"]:
                    total_tokens += item["usage"]["completion_tokens"]
            if total_tokens > 0:
                client.update_usage(model_name, total_tokens)
        except Exception as e:
            log_msg("quota_update_failed", e=e)

    async def _run_prebuilt_content(
        self,
        client,
        node_id,
        model_name,
        content,
        estimation_duration,
        resolution,
        generation_count,
        filename_prefix,
        save_last_frame_batch,
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
        client.check_quota(
            model_name,
            self._estimate_video_tokens(model_name, resolution, estimation_duration)
            * generation_count,
        )
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
            return comfy_io.NodeOutput(
                None, None, json.dumps(successful_tasks, ensure_ascii=False, indent=2)
            )

        if not successful_tasks and ignore_errors:
            dummy_video = create_white_video(1024, 1024)
            dummy_frame = create_white_image_tensor(1024, 1024)
            return comfy_io.NodeOutput(dummy_video, dummy_frame, json.dumps({"error": "All tasks failed but ignored. Returning dummy video/image."}))

        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(force_close=True)
        ) as session:
            ret_results = await self._handle_batch_success_async(
                successful_tasks,
                filename_prefix,
                generation_count,
                save_last_frame_batch,
                session,
            )
            await asyncio.sleep(0.25)
        self._record_usage(client, model_name, ret_results)
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
        filename_prefix,
        save_last_frame_batch,
        non_blocking,
        node_id,
        model_name,
        content,
        forbidden_params,
        service_tier="default",
        execution_expires_after=None,
        enable_random_seed=False,
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

            api_seed = seed
            if enable_random_seed:
                api_seed = -1

            if extra_api_params is None:
                extra_api_params = {}

            if resolution is not None:
                extra_api_params["resolution"] = resolution
            if aspect_ratio is not None:
                extra_api_params["ratio"] = aspect_ratio
            if api_seed is not None:
                extra_api_params["seed"] = api_seed

            estimation_duration = 5
            if is_auto_duration:
                extra_api_params["duration"] = -1
            else:
                key, val, est = _calculate_duration_and_frames_args(duration)
                extra_api_params[key] = val
                estimation_duration = est
            
            est_tokens_per_video = self._estimate_video_tokens(
                model_name,
                resolution,
                estimation_duration,
                has_audio=extra_api_params.get("generate_audio", False),
                is_draft=extra_api_params.get("draft", False),
            )
            client.check_quota(model_name, est_tokens_per_video * generation_count)

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
                return comfy_io.NodeOutput(
                    None,
                    None,
                    json.dumps(successful_tasks, ensure_ascii=False, indent=2),
                )

            if not successful_tasks and ignore_errors:
                 dummy_video = create_white_video(1024, 1024)
                 dummy_frame = create_white_image_tensor(1024, 1024)
                 return comfy_io.NodeOutput(dummy_video, dummy_frame, json.dumps({"error": "All tasks failed but ignored. Returning dummy video/image."}))

            ret_results = None
            async with aiohttp.ClientSession() as session:
                ret_results = await self._handle_batch_success_async(
                    successful_tasks,
                    filename_prefix,
                    generation_count,
                    save_last_frame_batch,
                    session,
                )
                await asyncio.sleep(0.25)
            
            self._record_usage(client, model_name, ret_results)
            return ret_results

        except Exception as e:
            if isinstance(e, comfy.model_management.InterruptProcessingException):
                raise e
            s_e = str(e)
            if s_e.startswith("[BytePlus]"):
                raise e
            raise BytePlusException(format_api_error(e))


class BytePlusSeedance1(BytePlusVideoBase, comfy_io.ComfyNode):
    """
    Seedance 1.0 Pro / Pro Fast video node.
    Text-to-video and first/last-frame image-to-video.
    """
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusSeedance1",
            display_name="BytePlus Seedance 1.0",
            category=GLOBAL_CATEGORY,
            is_output_node=True,
            inputs=[
                BytePlusClientType.Input("client"),
                comfy_io.Combo.Input(
                    "model_version",
                    options=VIDEO_1_UI_OPTIONS,
                    default=VIDEO_1_UI_OPTIONS[0],
                ),
                comfy_io.String.Input("prompt", multiline=True, default=""),
            ]
            + get_common_video_seed_inputs()
            + [
                get_resolution_input(default="720p", support_1080p=True),
                get_aspect_ratio_input(default="adaptive", include_adaptive=True),
                get_duration_input(
                    default=5.0, min_val=1.2, max_val=12.0, step=0.2, is_int=False
                ),
                comfy_io.Boolean.Input("camerafixed", default=True),
            ]
            + get_common_video_runtime_inputs(include_offline=True)
            + [
                comfy_io.Image.Input("image", optional=True),
                comfy_io.Image.Input("last_frame_image", optional=True),
            ],
            hidden=[comfy_io.Hidden.unique_id, comfy_io.Hidden.prompt],
            outputs=[
                comfy_io.Video.Output(display_name="video"),
                comfy_io.Image.Output(display_name="last_frame"),
                comfy_io.String.Output(display_name="response"),
            ],
        )

    @classmethod
    async def execute(
        cls,
        client,
        model_version,
        prompt,
        duration,
        resolution,
        aspect_ratio,
        camerafixed,
        enable_random_seed,
        seed,
        generation_count,
        filename_prefix,
        save_last_frame_batch,
        enable_offline_inference,
        non_blocking,
        image=None,
        last_frame_image=None,
    ) -> comfy_io.NodeOutput:

        node_id = cls.hidden.unique_id

        if image is None and aspect_ratio == "adaptive":
            aspect_ratio = "16:9"

        final_model_name = resolve_model_id(model_version)

        helper = BytePlusVideoBase()
        helper.NON_BLOCKING_TASK_CACHE = cls.NON_BLOCKING_TASK_CACHE

        helper._validate_reference_image_constraints(image)
        helper._validate_reference_image_constraints(last_frame_image)

        content = []
        total_image_request_bytes = 0
        total_image_request_bytes += helper._append_image_content(content, image, "first_frame")

        if last_frame_image is not None:
            if image is None:
                raise BytePlusException(get_text("popup_first_frame_missing"))
            total_image_request_bytes += helper._append_image_content(content, last_frame_image, "last_frame")

        total_image_request_mb = float(total_image_request_bytes) / (1024.0 * 1024.0)
        if total_image_request_mb > REF_IMAGE_MAX_TOTAL_REQUEST_MB:
            raise BytePlusException(
                get_text("popup_ref_image_total_size_exceeded").format(
                    max_mb=REF_IMAGE_MAX_TOTAL_REQUEST_MB, size_mb=f"{total_image_request_mb:.3f}"
                )
            )

        service_tier, execution_expires_after = helper._get_service_options(
            enable_offline_inference, VIDEO_DEFAULT_TIMEOUT
        )

        return await helper._common_generation_logic(
            client,
            prompt,
            duration,
            resolution,
            aspect_ratio,
            seed,
            generation_count,
            filename_prefix,
            save_last_frame_batch,
            non_blocking,
            node_id,
            model_name=final_model_name,
            content=content,
            forbidden_params=[
                "resolution",
                "ratio",
                "dur",
                "frames",
                "camerafixed",
                "seed",
            ],
            extra_api_params={"camera_fixed": camerafixed},
            service_tier=service_tier,
            execution_expires_after=execution_expires_after,
            enable_random_seed=enable_random_seed,
            node_class_type="BytePlusSeedance1",
            workflow_prompt=cls.hidden.prompt,
        )


class BytePlusSeedance1_5(BytePlusVideoBase, comfy_io.ComfyNode):
    """
    Seedance 1.5 Pro video node.
    Text-to-video, image-to-video, draft mode and draft reuse.
    """
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusSeedance1_5",
            display_name="BytePlus Seedance 1.5 Pro",
            category=GLOBAL_CATEGORY,
            is_output_node=True,
            inputs=[
                BytePlusClientType.Input("client"),
                comfy_io.Combo.Input(
                    "model_version",
                    options=VIDEO_1_5_UI_OPTIONS,
                    default=VIDEO_1_5_UI_OPTIONS[0],
                ),
                comfy_io.String.Input("prompt", multiline=True, default=""),
            ]
            + get_common_video_seed_inputs()
            + [
                get_resolution_input(default="720p", support_1080p=True),
                get_aspect_ratio_input(default="adaptive", include_adaptive=True),
                comfy_io.Boolean.Input("auto_duration", default=False),
                get_duration_input(default=5, min_val=4, max_val=12, is_int=True),
                comfy_io.Boolean.Input("generate_audio", default=True),
                comfy_io.Boolean.Input("draft_mode", default=False),
                comfy_io.Boolean.Input("reuse_last_draft_task", default=False),
                comfy_io.String.Input("draft_task_id", default=""),
                comfy_io.Boolean.Input("camerafixed", default=True),
            ]
            + get_common_video_runtime_inputs(include_offline=True)
            + [
                comfy_io.Image.Input("image", optional=True),
                comfy_io.Image.Input("last_frame_image", optional=True),
            ],
            hidden=[comfy_io.Hidden.unique_id, comfy_io.Hidden.prompt],
            outputs=[
                comfy_io.Video.Output(display_name="video"),
                comfy_io.Image.Output(display_name="last_frame"),
                comfy_io.String.Output(display_name="response"),
            ],
        )

    @classmethod
    async def execute(
        cls,
        client,
        model_version,
        prompt,
        generate_audio,
        auto_duration,
        duration,
        resolution,
        aspect_ratio,
        camerafixed,
        enable_random_seed,
        seed,
        generation_count,
        filename_prefix,
        save_last_frame_batch,
        enable_offline_inference,
        non_blocking,
        draft_mode,
        reuse_last_draft_task,
        draft_task_id,
        image=None,
        last_frame_image=None,
    ) -> comfy_io.NodeOutput:

        node_id = cls.hidden.unique_id

        global LAST_SEEDANCE_1_5_DRAFT_TASK_ID

        content_for_reuse = None
        reuse_count = generation_count
        draft_ids = select_draft_task_ids(
            draft_mode,
            reuse_last_draft_task,
            draft_task_id,
            LAST_SEEDANCE_1_5_DRAFT_TASK_ID.get(node_id),
        )
        if draft_ids:
            content_for_reuse, reuse_count = build_draft_final_content(
                draft_ids, generation_count
            )

        final_model_name = resolve_model_id(model_version)

        helper = BytePlusVideoBase()
        helper.NON_BLOCKING_TASK_CACHE = cls.NON_BLOCKING_TASK_CACHE

        helper._validate_reference_image_constraints(image)
        helper._validate_reference_image_constraints(last_frame_image)

        service_tier, execution_expires_after = helper._get_service_options(
            enable_offline_inference, VIDEO_DEFAULT_TIMEOUT
        )

        node_count = get_node_count_in_workflow("BytePlusSeedance1_5", prompt=cls.hidden.prompt)
        # log_msg("debug_node_count", count=node_count, type="BytePlusSeedance1_5")
        ignore_errors = node_count > 1

        if content_for_reuse:
            return await helper._run_prebuilt_content(
                client,
                node_id,
                final_model_name,
                content_for_reuse,
                estimation_duration=5 if auto_duration else float(duration),
                resolution=resolution,
                generation_count=reuse_count,
                filename_prefix=filename_prefix,
                save_last_frame_batch=save_last_frame_batch,
                non_blocking=non_blocking,
                extra_api_params={"resolution": resolution},
                service_tier=service_tier,
                execution_expires_after=execution_expires_after,
                ignore_errors=ignore_errors,
            )

        content = []
        total_image_request_bytes = 0
        total_image_request_bytes += helper._append_image_content(content, image, "first_frame")

        if last_frame_image is not None:
            if image is None:
                raise BytePlusException(get_text("popup_first_frame_missing"))
            total_image_request_bytes += helper._append_image_content(content, last_frame_image, "last_frame")

        total_image_request_mb = float(total_image_request_bytes) / (1024.0 * 1024.0)
        if total_image_request_mb > REF_IMAGE_MAX_TOTAL_REQUEST_MB:
            raise BytePlusException(
                get_text("popup_ref_image_total_size_exceeded").format(
                    max_mb=REF_IMAGE_MAX_TOTAL_REQUEST_MB, size_mb=f"{total_image_request_mb:.3f}"
                )
            )

        final_duration = -1.0 if auto_duration else float(duration)

        extra_api_params = {}
        should_return_last_frame = True
        final_resolution = resolution

        if draft_mode:
            extra_api_params["draft"] = True
            final_resolution = "480p"
            should_return_last_frame = False
            service_tier = "default"

        extra_api_params["camera_fixed"] = camerafixed
        extra_api_params["generate_audio"] = generate_audio

        def _on_tasks_created(tasks):
            if draft_mode:
                try:
                    global LAST_SEEDANCE_1_5_DRAFT_TASK_ID
                    if tasks and len(tasks) > 0:
                        LAST_SEEDANCE_1_5_DRAFT_TASK_ID[node_id] = [t.id for t in tasks]
                except Exception as e:
                    print(f"[BytePlus] Failed to record draft task ID: {e}")

        result = await helper._common_generation_logic(
            client,
            prompt,
            final_duration,
            final_resolution,
            aspect_ratio,
            seed,
            generation_count,
            filename_prefix,
            save_last_frame_batch if not draft_mode else False,
            non_blocking,
            node_id,
            model_name=final_model_name,
            content=content,
            forbidden_params=[
                "resolution",
                "ratio",
                "dur",
                "frames",
                "camerafixed",
                "seed",
                "generate_audio",
            ],
            service_tier=service_tier,
            execution_expires_after=execution_expires_after,
            enable_random_seed=enable_random_seed,
            is_auto_duration=auto_duration,
            extra_api_params=extra_api_params,
            return_last_frame=should_return_last_frame,
            on_tasks_created=_on_tasks_created,
            node_class_type="BytePlusSeedance1_5",
            workflow_prompt=cls.hidden.prompt,
        )

        return result


class BytePlusSeedance2(BytePlusVideoBase, comfy_io.ComfyNode):
    """
    Dreamina Seedance 2.0 / 2.5 / 2.5 Premium video node.
    Multimodal references (image, video, audio), video editing and extension,
    and (2.5 family) draft mode.
    """

    @staticmethod
    def _model_inputs(model_version):
        max_duration = VIDEO_2_MODEL_MAX_DURATIONS.get(model_version, 15)
        seedance_2_5_inputs = []
        if model_version in SEEDANCE_2_5_FAMILY:
            final_resolutions = " / ".join(SEEDANCE_DRAFT_FINAL_RESOLUTIONS[model_version])
            seedance_2_5_inputs = [
                comfy_io.Combo.Input(
                    "task_type",
                    options=SEEDANCE_2_5_TASK_TYPES,
                    default="auto",
                    tooltip=(
                        "Omni-reference task type. edit needs a reference video, "
                        "adaptive ratio and auto duration; extend needs a reference "
                        "video and adaptive ratio; reference has no extra limits."
                    ),
                ),
                comfy_io.Combo.Input(
                    "output_format",
                    options=SEEDANCE_2_5_OUTPUT_FORMATS,
                    default="mp4",
                    tooltip="mov keeps higher color precision for post-production.",
                ),
                comfy_io.Boolean.Input(
                    "draft_mode",
                    default=False,
                    tooltip=(
                        "Generate a quick 480p draft first. To render the final "
                        f"video ({final_resolutions}), set draft_task_id or enable "
                        "reuse_last_draft_task."
                    ),
                ),
                comfy_io.Boolean.Input(
                    "reuse_last_draft_task",
                    default=False,
                    tooltip="Render the final video from this node's last draft.",
                ),
                comfy_io.String.Input(
                    "draft_task_id",
                    default="",
                    tooltip=(
                        "Draft task ID(s), one per line, to render as final videos. "
                        "The prompt, references, duration, ratio, seed and audio "
                        "setting are reused from the draft. Valid for 7 days."
                    ),
                ),
            ]
        return [
            comfy_io.String.Input("prompt", multiline=True, default=""),
            *get_common_video_seed_inputs(),
            get_resolution_input(
                default="720p", options=get_seedance2_resolutions(model_version)
            ),
            get_aspect_ratio_input(default="adaptive", include_adaptive=True),
            comfy_io.Boolean.Input("auto_duration", default=False),
            get_duration_input(default=5, min_val=4, max_val=max_duration, is_int=True),
            comfy_io.Boolean.Input("generate_audio", default=True),
            *seedance_2_5_inputs,
            *get_common_video_runtime_inputs(include_offline=False),
        ]

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusSeedance2",
            display_name="BytePlus Seedance 2 / 2.5 (Legacy)",
            category=GLOBAL_CATEGORY,
            is_deprecated=True,
            description=(
                "Legacy node: use BytePlus Seedance 2.5 Text to Video, First-Last-Frame "
                "to Video or Reference to Video instead. "
                "Generate or edit video with Dreamina Seedance 2.0, Fast, Mini, 2.5, or "
                "2.5 Premium (4K). Seedance 2.5 supports up to 30-second output, more "
                "references and a 480p draft mode. "
                "Local reference videos are uploaded to Comfy.org storage (requires "
                "a Comfy.org login); or pass public video links in ref_video_urls. "
                "ref_image_urls / ref_audio_urls take links or asset:// IDs, e.g. "
                "Virtual Portraits from the asset library."
            ),
            is_output_node=True,
            is_experimental=True,
            inputs=[
                BytePlusClientType.Input("client"),
                comfy_io.DynamicCombo.Input(
                    "model_version",
                    options=[
                        comfy_io.DynamicCombo.Option(
                            model_version, cls._model_inputs(model_version)
                        )
                        for model_version in VIDEO_2_UI_OPTIONS
                    ],
                ),
                comfy_io.Image.Input("first_frame_image", optional=True),
                comfy_io.Image.Input("last_frame_image", optional=True),
                _create_named_autogrow_input(
                    "ref_images",
                    comfy_io.Image.Input("ref_image", optional=True),
                    [f"ref_image_{idx}" for idx in range(1, 31)],
                    1,
                ),
                _create_named_autogrow_input(
                    "ref_videos",
                    comfy_io.Video.Input("ref_video", optional=True),
                    [f"ref_video_{idx}" for idx in range(1, 11)],
                    1,
                ),
                _create_named_autogrow_input(
                    "ref_audios",
                    comfy_io.Audio.Input("ref_audio", optional=True),
                    [f"ref_audio_{idx}" for idx in range(1, 11)],
                    1,
                ),
                comfy_io.String.Input(
                    "ref_video_urls",
                    multiline=True,
                    default="",
                    optional=True,
                    tooltip=(
                        "Public mp4/mov links or asset:// IDs, one per line. Used "
                        "as reference videos without uploading to Comfy.org."
                    ),
                ),
                comfy_io.String.Input(
                    "ref_image_urls",
                    multiline=True,
                    default="",
                    optional=True,
                    tooltip=(
                        "Reference images as HTTPS links or asset:// IDs (e.g. a Virtual "
                        "Portrait from the asset library), one per line. In the prompt, refer "
                        "to them by position after connected ref images (Image 1, Image 2, ...)."
                    ),
                ),
                comfy_io.String.Input(
                    "ref_audio_urls",
                    multiline=True,
                    default="",
                    optional=True,
                    tooltip="Reference audio as HTTPS links or asset:// IDs, one per line.",
                ),
            ],
            hidden=[
                comfy_io.Hidden.auth_token_comfy_org,
                comfy_io.Hidden.api_key_comfy_org,
                comfy_io.Hidden.unique_id,
                comfy_io.Hidden.prompt,
            ],
            outputs=[
                comfy_io.Video.Output(display_name="video"),
                comfy_io.Image.Output(display_name="last_frame"),
                comfy_io.String.Output(display_name="response"),
            ],
        )

    @classmethod
    async def execute(
        cls,
        client,
        model_version,
        prompt="",
        generate_audio=True,
        task_type="auto",
        output_format="mp4",
        draft_mode=False,
        reuse_last_draft_task=False,
        draft_task_id="",
        auto_duration=False,
        duration=5,
        resolution="720p",
        aspect_ratio="adaptive",
        enable_random_seed=True,
        seed=0,
        generation_count=1,
        filename_prefix=DEFAULT_FILENAME_PREFIX,
        save_last_frame_batch=False,
        non_blocking=False,
        first_frame_image=None,
        last_frame_image=None,
        ref_images=None,
        ref_videos=None,
        ref_audios=None,
        ref_video_urls="",
        ref_image_urls="",
        ref_audio_urls="",
        **kwargs,
    ) -> comfy_io.NodeOutput:
        node_id = cls.hidden.unique_id

        if isinstance(model_version, dict):
            model_config = model_version
            model_version = model_config.get("model_version", VIDEO_2_UI_OPTIONS[0])
            prompt = model_config.get("prompt", prompt)
            generate_audio = model_config.get("generate_audio", generate_audio)
            task_type = model_config.get("task_type", task_type)
            output_format = model_config.get("output_format", output_format)
            draft_mode = model_config.get("draft_mode", draft_mode)
            reuse_last_draft_task = model_config.get(
                "reuse_last_draft_task", reuse_last_draft_task
            )
            draft_task_id = model_config.get("draft_task_id", draft_task_id)
            auto_duration = model_config.get("auto_duration", auto_duration)
            duration = model_config.get("duration", duration)
            resolution = model_config.get("resolution", resolution)
            aspect_ratio = model_config.get("aspect_ratio", aspect_ratio)
            enable_random_seed = model_config.get(
                "enable_random_seed", enable_random_seed
            )
            seed = model_config.get("seed", seed)
            generation_count = model_config.get("generation_count", generation_count)
            filename_prefix = model_config.get("filename_prefix", filename_prefix)
            save_last_frame_batch = model_config.get(
                "save_last_frame_batch", save_last_frame_batch
            )
            non_blocking = model_config.get("non_blocking", non_blocking)

        is_seedance_2_5 = model_version in SEEDANCE_2_5_FAMILY
        helper = BytePlusVideoBase()
        helper.NON_BLOCKING_TASK_CACHE = cls.NON_BLOCKING_TASK_CACHE

        if is_seedance_2_5:
            cached = LAST_SEEDANCE_2_DRAFT_TASKS.get(node_id) or {}
            draft_ids = select_draft_task_ids(
                draft_mode,
                reuse_last_draft_task,
                draft_task_id,
                cached.get("ids") if cached.get("model") == model_version else None,
            )
            if draft_ids:
                return await cls._render_final_from_drafts(
                    helper,
                    client,
                    node_id,
                    model_version,
                    draft_ids,
                    resolution=resolution,
                    output_format=output_format,
                    duration=duration,
                    auto_duration=auto_duration,
                    generation_count=generation_count,
                    filename_prefix=filename_prefix,
                    save_last_frame_batch=save_last_frame_batch,
                    non_blocking=non_blocking,
                )
        is_draft = is_seedance_2_5 and bool(draft_mode)
        if is_draft:
            resolution = SEEDANCE_DRAFT_RESOLUTION

        validate_seedance2_resolution(model_version, resolution, draft_mode=is_draft)
        duration = validate_seedance2_duration(model_version, duration, auto_duration)

        content = []
        total_image_request_bytes = 0
        ref_images = _collect_dynamic_inputs(ref_images, kwargs, "ref_image_")
        ref_videos = _collect_dynamic_inputs(ref_videos, kwargs, "ref_video_")
        ref_audios = _collect_dynamic_inputs(ref_audios, kwargs, "ref_audio_")
        linked_video_urls = _parse_reference_urls(ref_video_urls)
        linked_image_urls = _parse_reference_urls(ref_image_urls)
        linked_audio_urls = _parse_reference_urls(ref_audio_urls)
        validate_seedance2_reference_counts(
            model_version,
            ref_images + linked_image_urls,
            ref_videos + linked_video_urls,
            ref_audios + linked_audio_urls,
        )
        if is_seedance_2_5:
            validate_seedance25_task_type(
                task_type,
                bool(ref_videos or linked_video_urls),
                aspect_ratio,
                auto_duration,
            )
            if first_frame_image is not None and aspect_ratio != "adaptive":
                raise BytePlusException(get_text("err_seedance25_first_frame_ratio"))
        reference_media_max_duration = (
            REF_MEDIA_MAX_DURATION_SEEDANCE_2_5
            if is_seedance_2_5
            else REF_VIDEO_MAX_DURATION
        )

        for img in [first_frame_image, last_frame_image] + ref_images:
            helper._validate_reference_image_constraints(img)

        total_image_request_bytes += helper._append_image_content(
            content, first_frame_image, "first_frame"
        )
        if last_frame_image is not None:
            if first_frame_image is None:
                raise BytePlusException(get_text("popup_first_frame_missing"))
            total_image_request_bytes += helper._append_image_content(
                content, last_frame_image, "last_frame"
            )

        has_any_reference_inputs = bool(
            ref_images or linked_image_urls or ref_videos or linked_video_urls
            or ref_audios or linked_audio_urls
        )

        if (first_frame_image is not None or last_frame_image is not None) and has_any_reference_inputs:
            raise BytePlusException(get_text("popup_first_last_conflict_with_refs"))

        helper._validate_reference_videos_constraints(
            ref_videos,
            ref_video_urls=[url for url in linked_video_urls if not _is_asset_uri(url)],
            max_duration=reference_media_max_duration,
            max_total_duration=reference_media_max_duration,
        )

        for img in ref_images:
            total_image_request_bytes += helper._append_image_content(
                content, img, "reference_image"
            )
        for image_url in linked_image_urls:
            helper._append_media_url_content(content, image_url, "image_url", "reference_image")

        total_image_request_mb = float(total_image_request_bytes) / (1024.0 * 1024.0)
        if total_image_request_mb > REF_IMAGE_MAX_TOTAL_REQUEST_MB:
            raise BytePlusException(
                get_text("popup_ref_image_total_size_exceeded").format(
                    max_mb=REF_IMAGE_MAX_TOTAL_REQUEST_MB, size_mb=f"{total_image_request_mb:.3f}"
                )
            )

        uploaded_video_urls = []
        if ref_videos:
            uploaded_video_urls = await upload_videos_to_comfy_storage_cached(cls, ref_videos, helper)

        final_video_urls = [
            (video_url or "").strip()
            for video_url in uploaded_video_urls + linked_video_urls
        ]
        for video_url in final_video_urls:
            helper._append_media_url_content(content, video_url, "video_url", "reference_video")

        total_audio_duration = 0.0
        total_audio_request_bytes = 0
        for audio in ref_audios:
            appended = helper._append_audio_content(
                content,
                audio,
                "reference_audio",
                max_duration=reference_media_max_duration,
            )
            if appended is None:
                continue
            audio_duration, request_bytes = appended
            total_audio_duration += audio_duration
            total_audio_request_bytes += request_bytes

        for audio_url in linked_audio_urls:
            helper._append_media_url_content(content, audio_url, "audio_url", "reference_audio")

        max_total_audio_duration = (
            REF_MEDIA_MAX_DURATION_SEEDANCE_2_5
            if is_seedance_2_5
            else REF_AUDIO_MAX_TOTAL_DURATION
        )
        if total_audio_duration > max_total_audio_duration:
            raise BytePlusException(
                get_text("popup_ref_audio_total_duration_exceeded").format(
                    max=max_total_audio_duration, duration=f"{total_audio_duration:.3f}"
                )
            )

        total_audio_request_mb = float(total_audio_request_bytes) / (1024.0 * 1024.0)
        if total_audio_request_mb > REF_AUDIO_MAX_TOTAL_REQUEST_MB:
            raise BytePlusException(
                get_text("popup_ref_audio_total_size_exceeded").format(
                    max_mb=REF_AUDIO_MAX_TOTAL_REQUEST_MB,
                    size_mb=f"{total_audio_request_mb:.3f}",
                )
            )

        has_image_reference = any(
            img is not None for img in [first_frame_image, last_frame_image]
        ) or bool(ref_images or linked_image_urls)
        has_video_reference = any(
            (url or "").strip()
            for url in final_video_urls
        )
        has_audio_reference = bool(ref_audios or linked_audio_urls)
        prompt = (prompt or "").strip()

        if not prompt and not content:
            raise BytePlusException(get_text("popup_video_prompt_or_ref_required"))

        if (
            has_audio_reference
            and not is_seedance_2_5
            and not (has_image_reference or has_video_reference)
        ):
            raise BytePlusException(
                get_text("popup_audio_requires_visual_ref")
            )

        if not has_image_reference and not has_video_reference and aspect_ratio == "adaptive":
            aspect_ratio = "16:9"

        final_duration = duration
        extra_api_params = {
            "generate_audio": generate_audio,
        }
        if is_seedance_2_5:
            if task_type != "auto":
                extra_api_params["omni_reference_task_type"] = task_type
            if output_format != "mp4":
                extra_api_params["output_format"] = output_format
        if is_draft:
            extra_api_params["draft"] = True

        def _on_tasks_created(tasks):
            if is_draft and tasks:
                LAST_SEEDANCE_2_DRAFT_TASKS[node_id] = {
                    "model": model_version,
                    "ids": [task.id for task in tasks],
                }

        return await helper._common_generation_logic(
            client,
            prompt,
            final_duration,
            resolution,
            aspect_ratio,
            seed,
            generation_count,
            filename_prefix,
            save_last_frame_batch,
            non_blocking,
            node_id,
            model_name=resolve_model_id(model_version),
            content=content,
            forbidden_params=[
                "resolution",
                "ratio",
                "dur",
                "frames",
                "seed",
                "generate_audio",
            ],
            enable_random_seed=enable_random_seed,
            is_auto_duration=auto_duration,
            extra_api_params=extra_api_params,
            return_last_frame=not is_draft,
            on_tasks_created=_on_tasks_created,
            node_class_type="BytePlusSeedance2",
            workflow_prompt=cls.hidden.prompt,
        )

    @classmethod
    async def _render_final_from_drafts(
        cls,
        helper,
        client,
        node_id,
        model_version,
        draft_ids,
        resolution,
        output_format,
        duration,
        auto_duration,
        generation_count,
        filename_prefix,
        save_last_frame_batch,
        non_blocking,
    ):
        """
        Render final videos from draft tasks. The model reuses the draft's
        prompt, references, duration, ratio, seed, audio and task type, so
        only the model, resolution and output settings are sent.
        """
        final_resolutions = SEEDANCE_DRAFT_FINAL_RESOLUTIONS[model_version]
        if resolution not in final_resolutions:
            raise BytePlusException(
                get_text(
                    "err_draft_final_resolution",
                    model=model_version,
                    resolution=resolution,
                    supported=", ".join(final_resolutions),
                )
            )
        content, count = build_draft_final_content(draft_ids, generation_count)
        extra_api_params = {"resolution": resolution}
        if output_format != "mp4":
            extra_api_params["output_format"] = output_format
        node_count = get_node_count_in_workflow("BytePlusSeedance2", prompt=cls.hidden.prompt)
        return await helper._run_prebuilt_content(
            client,
            node_id,
            resolve_model_id(model_version),
            content,
            estimation_duration=5 if auto_duration else float(duration),
            resolution=resolution,
            generation_count=count,
            filename_prefix=filename_prefix,
            save_last_frame_batch=save_last_frame_batch,
            non_blocking=non_blocking,
            extra_api_params=extra_api_params,
            ignore_errors=node_count > 1,
        )


class BytePlusProgressTest(comfy_io.ComfyNode):
    """
    Dev-only progress bar test node.
    Simulates progress events locally (no generation) to debug the frontend.
    """
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        test_model_options = (
            ["None"] + VIDEO_1_UI_OPTIONS + VIDEO_1_5_UI_OPTIONS + VIDEO_2_UI_OPTIONS
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
                BytePlusClientType.Input("client", optional=True),
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
        client=None,
        test_model="None",
        test_resolution="720p",
    ) -> comfy_io.NodeOutput:
        node_id = cls.hidden.unique_id
        ps_instance = PromptServer.instance

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
                BytePlusClientType.Input("client"),
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
