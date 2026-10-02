"""
BytePlus VOD AI MediaKit nodes: the MediaKit Client (its own API key),
vCube Video Enhance, shaped like ComfyUI core's ByteDanceVideoEnhanceNode
(comfy_api_nodes/nodes_bytedance.py), and Video Smoothness Enhance and Image
Quality Enhance (not in core).

Core calls MediaKit through the Comfy.org proxy; these nodes call
https://mediakit.<region>.bytepluses.com/api/v1 directly with a MediaKit API key
(Authorization: Bearer), which is a different key from the ModelArk and Seed
Speech keys. Video tools are asynchronous tasks: POST /tools/<tool>, then poll
GET /tasks/{task_id} until completed. Image Quality Enhance is synchronous
(POST /tools-sync/enhance-image answers with the result).

This pack adds before/after comparisons: for vCube, a video whose divider
sweeps across the frame plus a frame pair for ComfyUI's Compare Images slider;
for Smoothness, the source and the repaired video side by side; for images,
the original resized to the result for the Compare Images slider.
"""
import asyncio
import hashlib
import io
import json
import math
import os
import time
import uuid
from fractions import Fraction

import aiohttp
import comfy.model_management
import folder_paths
import torch
from comfy_api.input_impl import VideoFromFile
from comfy_api.latest import io as comfy_io
from comfy_execution.graph_utils import ExecutionBlocker

from .constants import (
    DEFAULT_MEDIAKIT_REGION,
    MEDIAKIT_API_KEY_ENV,
    MEDIAKIT_API_KEYS_CONSOLE_URL,
    MEDIAKIT_DOWNLOAD_STALL_SECONDS,
    MEDIAKIT_MAX_POLL_ERRORS,
    MEDIAKIT_POLL_SECONDS,
    MEDIAKIT_REGION_BASE_URLS,
    MEDIAKIT_REQUEST_TIMEOUT_SECONDS,
    MEDIAKIT_SUBMIT_RETRIES,
    MEDIAKIT_SYNC_TIMEOUT_SECONDS,
)
from .core_style import core_search_aliases
from . import credentials
from .nodes_shared import (
    with_default_client,
    GLOBAL_CATEGORY,
    LOG_PREFIX,
    ApiKeyStore,
    BytePlusException,
    _notify_api_key_saved,
    gather_cancelling,
    get_text,
    log_msg,
    plain_text,
    send_node_text,
    sleep_interruptible,
    upload_bytes_to_comfy_storage,
    wait_interruptible,
)

MEDIAKIT_CATEGORY = f"{GLOBAL_CATEGORY}/MediaKit"
ENV_KEY_OPTION = f"Environment ({MEDIAKIT_API_KEY_ENV})"
MEDIAKIT_CLIENT_TOOLTIP = (
    'Optional. Without it the node uses the AI MediaKit key from Settings > BytePlus (BYTEPLUS_VOD_MEDIAKIT_API_KEY or user/.env). Connect a BytePlus MediaKit Client node to use another key.'
)


def build_default_mediakit_client():
    """MediaKitClient for the default AI MediaKit key; raises when none is set."""
    api_key = credentials.get_setting(MEDIAKIT_API_KEY_ENV)
    if not api_key:
        # mediakit_api_keys.json is only used when it leaves no doubt which key is meant.
        MEDIAKIT_API_KEY_STORE.load()
        names = MEDIAKIT_API_KEY_STORE.get_key_names()
        if len(names) == 1:
            api_key = MEDIAKIT_API_KEY_STORE.find_api_key(names[0])
    if not api_key:
        raise BytePlusException(get_text("err_no_default_mediakit_key", path=credentials.env_file_path()))
    return MediaKitClient(api_key, DEFAULT_MEDIAKIT_REGION)



# MediaKit keys are their own product key, so they get their own file
# (git-ignored runtime file in the repo root) and socket type.
MEDIAKIT_API_KEYS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mediakit_api_keys.json"
)
MEDIAKIT_API_KEY_STORE = ApiKeyStore(MEDIAKIT_API_KEYS_FILE)

BytePlusMediaKitClientType = comfy_io.Custom("BYTEPLUS_MEDIAKIT_CLIENT")

# Core's vCube limits and options.
VCUBE_MAX_DURATION_SECONDS = 600
VCUBE_MIN_FPS = 15.0
VCUBE_MAX_FPS = 120.0
VCUBE_MIN_SHORT_SIDE = 128
VCUBE_MAX_SHORT_SIDE = 4320
VCUBE_MAX_INPUT_SHORT_SIDE = 1440
VCUBE_MAX_INPUT_LONG_SIDE = 2560
VCUBE_RESOLUTION_PRESETS = ["1080p", "720p", "2k", "4k", "8k"]
VCUBE_FPS_PRESETS = ["source", "24", "25", "30", "48", "50", "60", "120"]
VCUBE_SCENES = ["aigc", "common", "ugc", "short_series", "old_film"]
VCUBE_STYLES = ["hd", "natural"]
VCUBE_MIN_BITRATE_KBPS = 10
VCUBE_MAX_BITRATE_KBPS = 150000

# Video Smoothness limits (AI MediaKit docs): up to 4K; repairs up to 35 s.
SMOOTH_MAX_SHORT_SIDE = 2160
SMOOTH_MAX_LONG_SIDE = 4096
SMOOTH_MAX_REPAIR_SECONDS = 35
SMOOTH_DETECT_ONLY = "detect only"

# Comparison videos: short side at most 1080 px, divider sweep period,
# side-by-side width at most 3840 px.
COMPARISON_MAX_SHORT_SIDE = 1080
COMPARISON_SWEEP_SECONDS = 4.0
SIDE_BY_SIDE_MAX_WIDTH = 3840


class MediaKitClient:
    """
    MediaKit API key and endpoint for the region. Passed between nodes on the
    BYTEPLUS_MEDIAKIT_CLIENT socket; never serialized into outputs.
    """

    def __init__(self, api_key, region=DEFAULT_MEDIAKIT_REGION):
        self.api_key = api_key
        self.region = region if region in MEDIAKIT_REGION_BASE_URLS else DEFAULT_MEDIAKIT_REGION
        self.base_url = MEDIAKIT_REGION_BASE_URLS[self.region]

    def __repr__(self):
        return f"MediaKitClient(region={self.region!r})"


def describe_mediakit_error(error, status=None, request_id=None):
    """A readable message from MediaKit's error object (code, message, param)."""
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code") or (f"HTTP {status}" if status else "Error"))
    message = str(error.get("message") or "")
    param = error.get("param")
    hint = ""
    if code in ("Unauthorized", "InvalidApiKey", "AuthenticationFailed") or status == 401:
        hint = plain_text("mediakit_hint_auth", url=MEDIAKIT_API_KEYS_CONSOLE_URL)
    return get_text(
        "err_mediakit_api",
        code=code,
        message=message,
        param=f" (param: {param})" if param else "",
        request_id=f" Request ID: {request_id}." if request_id else "",
        hint=hint,
    )


async def _send(client, method, path, body=None, timeout_seconds=None):
    """One HTTP call to MediaKit; returns (status, parsed JSON or None). Tests replace this."""
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or MEDIAKIT_REQUEST_TIMEOUT_SECONDS)
    headers = {"Authorization": f"Bearer {client.api_key}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, client.base_url + path, headers=headers, json=body) as response:
            text = await response.text()
            try:
                data = json.loads(text) if text else None
            except ValueError:
                data = None
            return response.status, data


class MediaKitRequestError(BytePlusException):
    """A failed MediaKit call. retryable: network errors, timeouts, 429 and 5xx."""

    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def _retryable_status(status):
    return status == 429 or status >= 500


async def mediakit_request(client, method, path, body=None, timeout_seconds=None, accept_failed_task=False):
    """
    Call MediaKit; raise MediaKitRequestError unless the response reports
    success. accept_failed_task returns a task whose status is 'failed'
    (whatever its success flag), so the poller can report it as such.
    """
    seconds = timeout_seconds or MEDIAKIT_REQUEST_TIMEOUT_SECONDS
    try:
        status, data = await wait_interruptible(_send(client, method, path, body, timeout_seconds=seconds))
    except comfy.model_management.InterruptProcessingException:
        raise
    except asyncio.TimeoutError:
        raise MediaKitRequestError(get_text("err_mediakit_timeout", seconds=seconds), retryable=True)
    except aiohttp.ClientError as e:
        raise MediaKitRequestError(get_text("err_mediakit_network", e=e), retryable=True)
    if not isinstance(data, dict):
        raise MediaKitRequestError(get_text("err_mediakit_unexpected", status=status), _retryable_status(status))
    if accept_failed_task and data.get("status") == "failed":
        return data
    if status >= 400 or data.get("success") is False:
        raise MediaKitRequestError(
            describe_mediakit_error(data.get("error"), status, data.get("request_id")), _retryable_status(status)
        )
    return data


async def wait_for_mediakit_task(client, task_id, node_id=None, poll_seconds=None):
    """
    Poll GET /tasks/{task_id} until completed (returns the task) or failed
    (raises). No client-side time limit: Professional runs can take hours;
    the wait is interruptible.
    """
    poll_seconds = MEDIAKIT_POLL_SECONDS if poll_seconds is None else poll_seconds
    started = time.monotonic()
    errors = 0
    while True:
        comfy.model_management.throw_exception_if_processing_interrupted()
        try:
            task = await mediakit_request(client, "GET", f"/tasks/{task_id}", accept_failed_task=True)
            errors = 0
        except MediaKitRequestError as e:
            # Only transient errors are retried; a bad key or unknown task fails at once.
            errors += 1
            if not e.retryable or errors >= MEDIAKIT_MAX_POLL_ERRORS:
                raise
            await sleep_interruptible(poll_seconds)
            continue
        status = str(task.get("status") or "")
        if status == "completed":
            return task
        if status == "failed":
            raise BytePlusException(
                get_text(
                    "err_mediakit_task_failed",
                    task_id=task_id,
                    detail=describe_mediakit_error(task.get("error")).replace(LOG_PREFIX, "", 1),
                )
            )
        elapsed = int(time.monotonic() - started)
        send_node_text(
            node_id,
            plain_text("mediakit_task_waiting", task_id=task_id, status=status or "running", elapsed=elapsed),
        )
        await sleep_interruptible(poll_seconds)


def task_result(task):
    """The result object of a completed task (the docs show it as result, sometimes output)."""
    result = task.get("result") or task.get("output") or {}
    return result if isinstance(result, dict) else {}


# --------------------------------------------------------------------------
# MediaKit Client
# --------------------------------------------------------------------------

class BytePlusMediaKitClient(comfy_io.ComfyNode):
    """
    BytePlus VOD AI MediaKit client: picks the MediaKit API key (not a
    ModelArk or Seed Speech key) for the MediaKit nodes.
    """

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        MEDIAKIT_API_KEY_STORE.load()
        key_names = MEDIAKIT_API_KEY_STORE.get_key_names() + [ENV_KEY_OPTION, "Custom"]
        return comfy_io.Schema(
            node_id="BytePlusMediaKitClient",
            display_name="BytePlus MediaKit Client",
            category=MEDIAKIT_CATEGORY,
            description=(
                "BytePlus VOD AI MediaKit API key for vCube Video Enhance, Video Smoothness Enhance and "
                "Image Quality Enhance. Create it on the AI MediaKit Settings page of the VOD console; "
                "ModelArk and Seed Speech keys do not work here."
            ),
            inputs=[
                comfy_io.String.Input("new_api_key", default=""),
                comfy_io.String.Input("new_key_name", default=""),
                comfy_io.Combo.Input(
                    "key_name",
                    options=key_names,
                    tooltip=(
                        f"Saved MediaKit key, the {MEDIAKIT_API_KEY_ENV} environment variable, or Custom "
                        "to paste a key (saved under new_key_name)."
                    ),
                ),
                comfy_io.Combo.Input(
                    "region",
                    options=list(MEDIAKIT_REGION_BASE_URLS.keys()),
                    default=DEFAULT_MEDIAKIT_REGION,
                    tooltip="AI MediaKit region.",
                ),
            ],
            outputs=[BytePlusMediaKitClientType.Output(display_name="mediakit_client")],
            hidden=[comfy_io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, key_name, new_api_key="", new_key_name="", region=DEFAULT_MEDIAKIT_REGION) -> comfy_io.NodeOutput:
        if key_name == "Custom":
            api_key = (new_api_key or "").strip()
            if not api_key:
                raise BytePlusException(get_text("mediakit_key_empty"))
            name = (new_key_name or "").strip()
            if name:
                if MEDIAKIT_API_KEY_STORE.upsert(name, api_key):
                    log_msg("mediakit_key_saved", name=name)
                    _notify_api_key_saved(cls.hidden.unique_id, name, api_key, store="mediakit")
                else:
                    # Keep the pasted key in the node so it is not lost.
                    log_msg("mediakit_key_save_failed", name=name)
        elif key_name == ENV_KEY_OPTION:
            api_key = credentials.get_setting(MEDIAKIT_API_KEY_ENV)
            if not api_key:
                raise BytePlusException(get_text("mediakit_env_key_missing", env=MEDIAKIT_API_KEY_ENV))
        else:
            MEDIAKIT_API_KEY_STORE.load()
            api_key = MEDIAKIT_API_KEY_STORE.find_api_key(key_name)
            if not api_key:
                raise BytePlusException(get_text("mediakit_key_not_found", key_name=key_name))
        return comfy_io.NodeOutput(MediaKitClient(api_key, region))


# --------------------------------------------------------------------------
# vCube Video Enhance: inputs, request and source limits
# --------------------------------------------------------------------------

def _style_input():
    return comfy_io.Combo.Input(
        "enhance_style",
        options=VCUBE_STYLES,
        default="hd",
        tooltip="'hd' applies a sharper enhancement; 'natural' reduces the strength "
        "for a softer, less sharpened look.",
    )


def _tool_version_input():
    return comfy_io.DynamicCombo.Input(
        "tool_version",
        options=[
            comfy_io.DynamicCombo.Option(
                "standard",
                [
                    comfy_io.Combo.Input(
                        "scene",
                        options=VCUBE_SCENES,
                        default="aigc",
                        tooltip="Preset tuned to the content: 'aigc' for AI-generated footage, "
                        "'common' for general video, 'ugc' for compressed phone clips, "
                        "'short_series' for drama with faces, 'old_film' for scratched or "
                        "flickering archive footage.",
                    ),
                    _style_input(),
                ],
            ),
            comfy_io.DynamicCombo.Option("professional", [_style_input()]),
        ],
        tooltip="'standard' balances speed and quality with 10+ enhancement algorithms. "
        "'professional' uses 30+ algorithms for cinema-grade restoration, takes about "
        "3x longer and costs 10x more.",
    )


def _resolution_input():
    return comfy_io.DynamicCombo.Input(
        "resolution",
        options=[
            *[comfy_io.DynamicCombo.Option(preset, []) for preset in VCUBE_RESOLUTION_PRESETS],
            comfy_io.DynamicCombo.Option("source", []),
            comfy_io.DynamicCombo.Option(
                "custom",
                [
                    comfy_io.Int.Input(
                        "short_side",
                        default=1080,
                        min=VCUBE_MIN_SHORT_SIDE,
                        max=VCUBE_MAX_SHORT_SIDE,
                        tooltip="Short side of the output in pixels; the long side follows "
                        "the source aspect ratio.",
                    ),
                ],
            ),
        ],
        tooltip="Output resolution. The short side is set to the chosen level and the long side "
        "follows the source aspect ratio. 'source' keeps the source size, 'custom' sets "
        "the short side in pixels. Sources wider or taller than about 2.2:1 are billed one "
        "resolution tier higher.",
    )


def build_enhance_request(video_url, tool_version, resolution, fps, bitrate_level, bitrate=0,
                          source_size=None, source_fps=None):
    """
    The POST /tools/enhance-video body, as core's node builds it: fps 'source'
    sends the source rate (when known and within 15-120), resolution 'source'
    sends the source short side as resolution_limit.
    """
    version = tool_version.get("tool_version", "standard")
    body = {"video_url": video_url, "tool_version": version}
    if version == "standard" and tool_version.get("scene"):
        body["scene"] = tool_version["scene"]
    if tool_version.get("enhance_style"):
        body["enhance_style"] = tool_version["enhance_style"]

    target = resolution.get("resolution", VCUBE_RESOLUTION_PRESETS[0])
    if target in VCUBE_RESOLUTION_PRESETS:
        body["resolution"] = target
    elif target == "custom":
        body["resolution_limit"] = int(resolution.get("short_side", 1080))
    elif target == "source" and source_size and min(source_size) >= VCUBE_MIN_SHORT_SIDE:
        body["resolution_limit"] = int(min(source_size))

    if fps == "source":
        if source_fps and source_fps >= VCUBE_MIN_FPS:
            body["fps"] = round(min(float(source_fps), VCUBE_MAX_FPS), 3)
    else:
        body["fps"] = float(fps)

    body["bitrate_level"] = bitrate_level
    if bitrate and int(bitrate) > 0:
        body["bitrate"] = int(bitrate)
    return body


def validate_source_video(video):
    """Core's checks: at most 600 s and 2560x1440 (2K) input."""
    try:
        duration = float(video.get_duration())
    except Exception:
        duration = 0.0
    if duration > VCUBE_MAX_DURATION_SECONDS:
        raise BytePlusException(
            get_text("err_vcube_too_long", duration=f"{duration:.1f}", max=VCUBE_MAX_DURATION_SECONDS)
        )
    width, height = video.get_dimensions()
    if min(width, height) > VCUBE_MAX_INPUT_SHORT_SIDE or max(width, height) > VCUBE_MAX_INPUT_LONG_SIDE:
        raise BytePlusException(
            get_text(
                "err_vcube_input_too_large",
                max_w=VCUBE_MAX_INPUT_LONG_SIDE,
                max_h=VCUBE_MAX_INPUT_SHORT_SIDE,
                width=width,
                height=height,
            )
        )
    return (int(width), int(height))


def _temp_path(prefix, ext="mp4"):
    folder = os.path.join(folder_paths.get_temp_directory(), "BytePlusMediaKit")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{prefix}_{uuid.uuid4().hex[:12]}.{ext}")


def _remove_quietly(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _reason(error):
    """A short reason for a failure, without URLs (signed links carry tokens in the query)."""
    if isinstance(error, aiohttp.ClientResponseError):
        return f"HTTP {error.status}"
    if isinstance(error, asyncio.TimeoutError):
        return "timed out"
    if isinstance(error, aiohttp.ClientError):
        return type(error).__name__
    return str(error).replace(LOG_PREFIX, "", 1).strip() or type(error).__name__


async def _download(url, prefix, ext="mp4"):
    """
    Stream url to a temp file. Results can be gigabytes, so only a stalled
    connection times out, not a long download. Interruptible.
    """
    from .utils_download import _download_to_file_stream_async

    path = _temp_path(prefix, ext)
    timeout = aiohttp.ClientTimeout(
        total=None, sock_connect=MEDIAKIT_REQUEST_TIMEOUT_SECONDS, sock_read=MEDIAKIT_DOWNLOAD_STALL_SECONDS
    )

    async def fetch():
        async with aiohttp.ClientSession(timeout=timeout) as session:
            return await _download_to_file_stream_async(session, url, path, timeout=timeout)

    reason = "no data"
    try:
        ok = await wait_interruptible(fetch())
    except comfy.model_management.InterruptProcessingException:
        _remove_quietly(path)
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        ok, reason = False, _reason(e)
    if not ok or not os.path.exists(path):
        _remove_quietly(path)
        raise BytePlusException(get_text("err_mediakit_download_failed", url=url.split("?", 1)[0], reason=reason))
    return path


# --- Source video and tasks (shared by the MediaKit tools) --------------------

def check_source(media, url, name="video_url", both_key="err_mediakit_source_both",
                 missing_key="err_mediakit_source_missing"):
    """The url link ("" for connected media). Exactly one source is required."""
    link = str(url or "").strip()
    if media is not None and link:
        raise BytePlusException(get_text(both_key))
    if media is None and not link:
        raise BytePlusException(get_text(missing_key))
    if link and not link.lower().startswith(("http://", "https://")):
        raise BytePlusException(get_text("err_mediakit_url_invalid", name=name, url=link))
    return link


async def upload_source(node_cls, video):
    """MediaKit only takes links, so a connected video goes through Comfy.org storage."""
    from .nodes_video import upload_videos_to_comfy_storage_cached

    urls = await upload_videos_to_comfy_storage_cached(
        node_cls,
        [video],
        unavailable_key="err_comfy_video_upload_unavailable_mediakit",
        failed_key="err_comfy_video_upload_failed_mediakit",
    )
    return urls[0]


async def source_file(video, link, prefix):
    """A local copy of the source video (for comparisons and pass-through); the caller owns the file."""
    if video is not None:
        path = _temp_path(prefix)
        await asyncio.to_thread(video.save_to, path)
        return path
    return await _download(link, prefix)


async def submit_and_wait(client, path, body, node_id, submitted_key):
    """
    POST a tool request and wait for its task; returns the completed task.

    Every run sends a fresh client_token. Without one, MediaKit treats the same
    account, video_url and core parameters (for vCube: tool_version and
    resolution) within 24 h as the same task, so a re-run with another fps,
    bitrate or style would get the earlier task back. Retrying with the same
    token returns the same task, so transient submit failures are retried
    without creating (and billing) a second one.
    """
    body = {**body, "client_token": body.get("client_token") or uuid.uuid4().hex}
    for attempt in range(MEDIAKIT_SUBMIT_RETRIES + 1):
        try:
            created = await mediakit_request(client, "POST", path, body)
            break
        except MediaKitRequestError as e:
            if not e.retryable or attempt >= MEDIAKIT_SUBMIT_RETRIES:
                raise
            await sleep_interruptible(2 ** attempt)
    task_id = created.get("task_id")
    if not task_id:
        raise BytePlusException(get_text("err_mediakit_unexpected", status="no task_id"))
    log_msg(submitted_key, task_id=task_id)
    return await wait_for_mediakit_task(client, task_id, node_id)


def task_response(task):
    return json.dumps({k: v for k, v in task.items() if k != "request_id"}, ensure_ascii=False)


async def make_comparison(build, video, link, prefix, *args):
    """
    build(source_path, *args) in a thread, on a temporary copy of the source.
    A failure (decoding, memory, a missing source) only skips the comparison,
    so the paid result is still returned. The copy is deleted afterwards.
    """
    source_path = None
    try:
        source_path = await source_file(video, link, prefix)
        return await asyncio.to_thread(build, source_path, *args)
    except comfy.model_management.InterruptProcessingException:
        raise
    except Exception as e:
        log_msg("mediakit_comparison_skipped", e=_reason(e))
        return None
    finally:
        _remove_quietly(source_path)


# --- Comparison --------------------------------------------------------------

def _even(value):
    value = max(2, int(round(value)))
    return value - value % 2


def comparison_size(width, height, max_short_side=COMPARISON_MAX_SHORT_SIDE):
    """The enhanced video's size, scaled down so its short side is at most max_short_side."""
    scale = min(1.0, float(max_short_side) / float(min(width, height)))
    return _even(width * scale), _even(height * scale)


def side_by_side_size(width, height, max_width=SIDE_BY_SIDE_MAX_WIDTH, max_short_side=COMPARISON_MAX_SHORT_SIDE):
    """Size of each half: short side at most max_short_side, both halves within max_width."""
    scale = min(1.0, float(max_short_side) / float(min(width, height)), (max_width / 2.0) / float(width))
    return _even(width * scale), _even(height * scale)


def sweep_position(t, period=COMPARISON_SWEEP_SECONDS, low=0.1, high=0.9):
    """Divider position (0-1 of the width) at time t: sweeps low->high->low every period."""
    phase = (t % period) / period
    tri = 1.0 - abs(2.0 * phase - 1.0)  # 0 -> 1 -> 0
    eased = 0.5 - 0.5 * math.cos(math.pi * tri)
    return low + (high - low) * eased


def compose_comparison_frame(source_rgb, enhanced_rgb, position, line_px=2):
    """Left of the divider: source; right: enhanced; a light divider line between them."""
    import numpy

    height, width = enhanced_rgb.shape[:2]
    x = int(round(position * width))
    frame = enhanced_rgb.copy()
    frame[:, :x] = source_rgb[:, :x]
    lo, hi = max(0, x - line_px // 2), min(width, x + (line_px + 1) // 2)
    frame[:, lo:hi] = numpy.array([240, 240, 240], dtype=frame.dtype)
    return frame


def compose_side_by_side_frame(source_rgb, result_rgb, line_px=2):
    """Source on the left, result on the right, a light line between them."""
    import numpy

    frame = numpy.hstack([source_rgb, result_rgb])
    x = source_rgb.shape[1]
    frame[:, max(0, x - line_px // 2):x + (line_px + 1) // 2] = numpy.array([240, 240, 240], dtype=frame.dtype)
    return frame


def _draw_labels(frame, labels=("Original", "Enhanced")):
    """Small corner labels; skipped quietly if PIL text drawing is unavailable."""
    try:
        import numpy
        from PIL import Image, ImageDraw

        image = Image.fromarray(frame)
        draw = ImageDraw.Draw(image)
        margin = max(8, frame.shape[0] // 60)
        try:
            from PIL import ImageFont

            font = ImageFont.load_default(size=max(14, frame.shape[0] // 36))
        except Exception:  # Pillow < 10.1: fixed-size bitmap font
            font = None
        for text, anchor_x in ((labels[0], margin), (labels[1], None)):
            box = draw.textbbox((0, 0), text, font=font)
            w, h = box[2] - box[0], box[3] - box[1]
            x = anchor_x if anchor_x is not None else frame.shape[1] - w - margin
            pad = max(4, h // 3)
            draw.rectangle([x - pad, margin - pad, x + w + pad, margin + h + pad + box[1]], fill=(0, 0, 0))
            draw.text((x, margin), text, fill=(255, 255, 255), font=font)
        return numpy.asarray(image)
    except Exception:
        return frame


def _frame_at(container, stream, t):
    """RGB frame of `stream` at (or just after) time t, decoding from the start."""
    container.seek(0)
    last = None
    for frame in container.decode(stream):
        last = frame
        if frame.time is not None and frame.time >= t:
            break
    return last


def _stream_info(container):
    """(video stream, width, height, duration in seconds or None, frame rate)."""
    import av

    stream = container.streams.video[0]
    duration = float(stream.duration * stream.time_base) if stream.duration else None
    if duration is None and container.duration:
        duration = container.duration / av.time_base
    rate = stream.average_rate or stream.guessed_rate
    if not rate or not 1 <= float(rate) <= 240:
        rate = Fraction(30)
    return stream, stream.codec_context.width, stream.codec_context.height, duration, rate


class _FrameCursor:
    """Walks a decoded stream in time order; rgb_at(t) is the frame on screen at time t."""

    def __init__(self, container, stream):
        container.seek(0)
        self._frames = container.decode(stream)
        self.current = next(self._frames, None)
        self.upcoming = next(self._frames, None)
        self._converted_frame = self._converted = None

    def frame_at(self, t):
        while self.upcoming is not None and self.upcoming.time is not None and self.upcoming.time <= t + 1e-6:
            self.current, self.upcoming = self.upcoming, next(self._frames, None)
        return self.current

    def ended_before(self, t, step):
        last = self.current
        return self.upcoming is None and (last is None or float(last.time or 0.0) + step <= t + 1e-6)

    def rgb_at(self, t, width, height):
        frame = self.frame_at(t)
        if frame is not None and frame is not self._converted_frame:
            self._converted = frame.reformat(width=width, height=height, format="rgb24").to_ndarray()
            self._converted_frame = frame
        return self._converted if frame is not None else None


def _render_synced(source, result, out_path, frame_size, compose):
    """
    Encode the result clip's timeline (its frame rate and duration), pairing
    each output frame with the source and result frames on screen at that
    time, so inserted, dropped or unevenly timed frames stay in sync.
    compose(source_rgb, result_rgb, t) returns the output frame.
    """
    import av

    r_stream, _width, _height, duration, rate = _stream_info(result)
    s_cursor = _FrameCursor(source, source.streams.video[0])
    r_cursor = _FrameCursor(result, r_stream)
    step = 1.0 / float(rate)
    count = max(1, int(round(duration * float(rate)))) if duration else None
    width, height = frame_size
    with av.open(out_path, mode="w") as output:
        out_stream = None
        index = 0
        while count is None or index < count:
            comfy.model_management.throw_exception_if_processing_interrupted()
            t = index * step
            after = r_cursor.rgb_at(t, width, height)
            if after is None or (count is None and r_cursor.ended_before(t, step)):
                break
            before = s_cursor.rgb_at(t, width, height)
            if before is None:
                break
            composed = compose(before, after, t)
            if out_stream is None:
                out_stream = output.add_stream("libx264", rate=rate)
                out_stream.height, out_stream.width = composed.shape[:2]
                out_stream.pix_fmt = "yuv420p"
                out_stream.options = {"crf": "18", "preset": "veryfast"}
            for packet in out_stream.encode(av.VideoFrame.from_ndarray(composed, format="rgb24")):
                output.mux(packet)
            index += 1
        if out_stream is None:
            raise BytePlusException(get_text("err_mediakit_comparison_no_frames"))
        for packet in out_stream.encode():
            output.mux(packet)


def build_comparison(source_path, enhanced_path, compare_time=-1.0):
    """
    vCube: (comparison video path, source frame tensor, enhanced frame tensor).
    The video follows the enhanced clip's timing at a short side of at most
    1080 px, with a divider sweeping across; the frames are full enhanced
    resolution, the source scaled to match.
    """
    import av
    import numpy

    with av.open(enhanced_path) as enhanced, av.open(source_path) as source:
        e_stream, width, height, duration, _rate = _stream_info(enhanced)

        # Still pair for Compare Images.
        t = (duration or 0.0) / 2.0 if compare_time is None or compare_time < 0 else float(compare_time)
        e_frame = _frame_at(enhanced, e_stream, t)
        s_frame = _frame_at(source, source.streams.video[0], t)
        if e_frame is None or s_frame is None:
            raise BytePlusException(get_text("err_mediakit_comparison_no_frames"))
        enhanced_still = e_frame.to_ndarray(format="rgb24")
        source_still = s_frame.reformat(width=width, height=height, format="rgb24").to_ndarray()

        out_path = _temp_path("vcube_comparison")
        _render_synced(
            source,
            enhanced,
            out_path,
            comparison_size(width, height),
            lambda before, after, ft: _draw_labels(compose_comparison_frame(before, after, sweep_position(ft))),
        )

    def to_tensor(array):
        return torch.from_numpy(numpy.ascontiguousarray(array)).float().div(255.0).unsqueeze(0)

    return out_path, to_tensor(source_still), to_tensor(enhanced_still)


def build_side_by_side(source_path, result_path, labels=("Original", "Smoothed")):
    """
    Video Smoothness: the source (left) and the repaired video (right) side by
    side on the repaired clip's timeline, so stutter and its repair play at
    the same moment. Each half has a short side of at most 1080 px.
    """
    import av

    with av.open(result_path) as result, av.open(source_path) as source:
        _stream, width, height, _duration, _rate = _stream_info(result)
        out_path = _temp_path("smooth_comparison")
        _render_synced(
            source,
            result,
            out_path,
            side_by_side_size(width, height),
            lambda before, after, _t: _draw_labels(compose_side_by_side_frame(before, after), labels),
        )
    return out_path


# --------------------------------------------------------------------------
# vCube Video Enhance
# --------------------------------------------------------------------------

class BytePlusVideoEnhance(comfy_io.ComfyNode):
    """Core's ByteDanceVideoEnhanceNode (vCube) on BytePlus VOD AI MediaKit."""

    NODE_ID = "BytePlusVideoEnhance"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id=cls.NODE_ID,
            display_name="BytePlus vCube Video Enhance",
            search_aliases=core_search_aliases(cls.NODE_ID),
            category=MEDIAKIT_CATEGORY,
            description="Upscales and restores a video with ByteDance vCube: super-resolution up to 8K, "
            "compression artifact and noise removal, colour and sharpness enhancement, "
            "optional frame interpolation.",
            inputs=[
                BytePlusMediaKitClientType.Input("mediakit_client", optional=True, tooltip=MEDIAKIT_CLIENT_TOOLTIP),
                comfy_io.Video.Input(
                    "video",
                    tooltip="Video to enhance. The source resolution must be at most 2560x1440 (2K); "
                    "the output size is set by the resolution input. Uploaded to Comfy.org storage "
                    "first (needs a Comfy.org login); or set video_url instead.",
                    optional=True,
                ),
                _tool_version_input(),
                _resolution_input(),
                comfy_io.Combo.Input(
                    "fps",
                    options=VCUBE_FPS_PRESETS,
                    default="source",
                    tooltip="Output frame rate. A higher rate than the source enables AI frame interpolation; "
                    "a lower one drops frames. 'source' keeps the source rate, up to 120 fps. "
                    "Rates above 30 fps cost 2x, above 60 fps 4x.",
                ),
                comfy_io.Combo.Input(
                    "bitrate_level",
                    options=["low", "medium", "high"],
                    default="medium",
                    advanced=True,
                    tooltip="Target bitrate of the delivered file, scaled to the output resolution and frame rate.",
                ),
                # This pack's extras.
                comfy_io.String.Input(
                    "video_url",
                    default="",
                    optional=True,
                    advanced=True,
                    tooltip="Public http(s) link to the source video, instead of connecting a video. "
                    "Nothing is uploaded to Comfy.org.",
                ),
                comfy_io.Int.Input(
                    "bitrate",
                    default=0,
                    min=0,
                    max=VCUBE_MAX_BITRATE_KBPS,
                    optional=True,
                    advanced=True,
                    tooltip="Exact target bitrate in kbps (10-150000); 0 uses bitrate_level.",
                ),
                comfy_io.Boolean.Input(
                    "comparison",
                    default=True,
                    optional=True,
                    advanced=True,
                    tooltip="Also build a before/after comparison video (a divider sweeps across the frame) "
                    "and a matching frame pair for the Compare Images node.",
                ),
                comfy_io.Float.Input(
                    "compare_time",
                    default=-1.0,
                    min=-1.0,
                    max=float(VCUBE_MAX_DURATION_SECONDS),
                    step=0.1,
                    optional=True,
                    advanced=True,
                    tooltip="Time in seconds of the source_frame / enhanced_frame pair; -1 takes the middle.",
                ),
            ],
            outputs=[
                comfy_io.Video.Output(),
                comfy_io.Video.Output(
                    "comparison",
                    tooltip="Before (left) and after (right) with a sweeping divider. "
                    "Nodes using it are skipped when comparison is off.",
                ),
                comfy_io.Image.Output(
                    "source_frame",
                    tooltip="A source frame scaled to the enhanced size; connect it and enhanced_frame "
                    "to Compare Images for a slider.",
                ),
                comfy_io.Image.Output("enhanced_frame", tooltip="The same moment from the enhanced video."),
                comfy_io.String.Output("response", tooltip="The MediaKit task as JSON."),
            ],
            hidden=[
                comfy_io.Hidden.auth_token_comfy_org,
                comfy_io.Hidden.api_key_comfy_org,
                comfy_io.Hidden.unique_id,
            ],
        )

    @classmethod
    @with_default_client("mediakit_client", build_default_mediakit_client)
    async def execute(
        cls,
        mediakit_client,
        tool_version,
        resolution,
        fps,
        bitrate_level,
        video=None,
        video_url="",
        bitrate=0,
        comparison=True,
        compare_time=-1.0,
    ) -> comfy_io.NodeOutput:
        link = check_source(video, video_url)
        if bitrate and not VCUBE_MIN_BITRATE_KBPS <= int(bitrate) <= VCUBE_MAX_BITRATE_KBPS:
            raise BytePlusException(
                get_text("err_vcube_bitrate", min=VCUBE_MIN_BITRATE_KBPS, max=VCUBE_MAX_BITRATE_KBPS, value=bitrate)
            )
        source_size = source_fps = None
        if video is not None:
            source_size = validate_source_video(video)
            try:
                source_fps = float(video.get_frame_rate())
            except Exception:
                source_fps = None
            link = await upload_source(cls, video)

        body = build_enhance_request(
            link, tool_version or {}, resolution or {}, fps, bitrate_level, bitrate, source_size, source_fps
        )
        task = await submit_and_wait(
            mediakit_client, "/tools/enhance-video", body, cls.hidden.unique_id, "vcube_task_submitted"
        )
        result_url = task_result(task).get("video_url")
        if not result_url:
            raise BytePlusException(get_text("err_mediakit_unexpected", status="completed without video_url"))
        enhanced_path = await _download(result_url, "vcube_enhanced")

        # Without a comparison, nodes using those outputs are skipped rather than failing on None.
        comparison_video = source_frame = enhanced_frame = ExecutionBlocker(None)
        if comparison:
            built = await make_comparison(build_comparison, video, link, "vcube_source", enhanced_path, compare_time)
            if built:
                comparison_path, source_frame, enhanced_frame = built
                comparison_video = VideoFromFile(comparison_path)

        return comfy_io.NodeOutput(
            VideoFromFile(enhanced_path), comparison_video, source_frame, enhanced_frame, task_response(task)
        )


# --------------------------------------------------------------------------
# Video Smoothness Enhance
# --------------------------------------------------------------------------

def _periodic_stutter_input():
    return comfy_io.DynamicCombo.Input(
        "periodic_stutter",
        options=[
            comfy_io.DynamicCombo.Option(
                "repair",
                [
                    comfy_io.Boolean.Input(
                        "align_source_fps",
                        default=False,
                        tooltip="Keep the source frame rate, frame count and duration: for each inserted frame, "
                        "a low-motion frame is removed. Off gives the best repair; the frame rate may rise.",
                    ),
                    comfy_io.String.Input(
                        "insert_frame_indices",
                        default="",
                        tooltip="Extra stutter points the detector may miss: zero-based frame numbers, "
                        "comma-separated. 80 inserts a frame between frames 80 and 81. These are always "
                        "inserted, even when MediaKit's quality check skips the automatic repair.",
                    ),
                ],
            ),
            comfy_io.DynamicCombo.Option(SMOOTH_DETECT_ONLY, []),
        ],
        tooltip="Periodic stutter: sudden jumps in the motion rhythm. 'repair' generates in-between frames; "
        "'detect only' counts them without changing the video.",
    )


def parse_frame_indices(text):
    """'80, 120' -> [80, 120]: distinct zero-based frame numbers, separated by commas or spaces."""
    value = str(text or "").strip()
    if not value:
        return []
    try:
        indices = [int(part) for part in value.replace(",", " ").split()]
    except ValueError:
        raise BytePlusException(get_text("err_smooth_frame_indices", value=value))
    if any(index < 0 for index in indices) or len(set(indices)) != len(indices):
        raise BytePlusException(get_text("err_smooth_frame_indices", value=value))
    return sorted(indices)


def build_smoothness_request(video_url, periodic_stutter, duplicate_frames):
    """
    The POST /tools/enhance-video-smoothness body. Both detection objects are
    always sent, so the request states what is repaired; align_source_fps and
    insert_frame_indices only apply while the stutter repair is on.
    """
    repair_stutter = periodic_stutter.get("periodic_stutter", "repair") != SMOOTH_DETECT_ONLY
    stutter = {"periodic_stutter_repair": repair_stutter}
    if repair_stutter:
        stutter["align_source_fps"] = bool(periodic_stutter.get("align_source_fps", False))
        indices = parse_frame_indices(periodic_stutter.get("insert_frame_indices", ""))
        if indices:
            stutter["insert_frame_indices"] = indices
    return {
        "video_url": video_url,
        "periodic_stutter_detect": stutter,
        "duplicate_frame_detect": {"duplicate_frame_repair": duplicate_frames != SMOOTH_DETECT_ONLY},
    }


def smoothness_repairs(body):
    return bool(
        body["periodic_stutter_detect"]["periodic_stutter_repair"]
        or body["duplicate_frame_detect"]["duplicate_frame_repair"]
    )


def validate_smoothness_source(video, repair):
    """Up to 4K; up to 35 s when anything is repaired (detection alone has no length limit)."""
    width, height = video.get_dimensions()
    if min(width, height) > SMOOTH_MAX_SHORT_SIDE or max(width, height) > SMOOTH_MAX_LONG_SIDE:
        raise BytePlusException(
            get_text(
                "err_smooth_input_too_large",
                max_w=SMOOTH_MAX_LONG_SIDE,
                max_h=SMOOTH_MAX_SHORT_SIDE,
                width=width,
                height=height,
            )
        )
    if repair:
        try:
            duration = float(video.get_duration())
        except Exception:
            duration = 0.0
        if duration > SMOOTH_MAX_REPAIR_SECONDS:
            raise BytePlusException(
                get_text("err_smooth_too_long", duration=f"{duration:.1f}", max=SMOOTH_MAX_REPAIR_SECONDS)
            )


def _detail_count(result, detail, field):
    value = result.get(detail)
    try:
        return int(value.get(field) or 0) if isinstance(value, dict) else 0
    except (TypeError, ValueError):
        return 0


class BytePlusVideoSmoothness(comfy_io.ComfyNode):
    """AI MediaKit Video Smoothness Enhancement: repairs periodic stutter and duplicate frames."""

    NODE_ID = "BytePlusVideoSmoothness"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id=cls.NODE_ID,
            display_name="BytePlus Video Smoothness Enhance",
            category=MEDIAKIT_CATEGORY,
            description="Repairs stutter without changing the resolution: generates in-between frames where the "
            "motion rhythm jumps (periodic stutter) and removes repeated frames. Made for Seedance videos with "
            "occasional stutter; also low-frame-rate or re-encoded video, screen and game recordings.",
            inputs=[
                BytePlusMediaKitClientType.Input("mediakit_client", optional=True, tooltip=MEDIAKIT_CLIENT_TOOLTIP),
                comfy_io.Video.Input(
                    "video",
                    tooltip="Video to repair: up to 4K, and up to 35 s while a repair is on (detect only has no "
                    "length limit). Uploaded to Comfy.org storage first (needs a Comfy.org login); or set "
                    "video_url instead.",
                    optional=True,
                ),
                _periodic_stutter_input(),
                comfy_io.Combo.Input(
                    "duplicate_frames",
                    options=["remove", SMOOTH_DETECT_ONLY],
                    default="remove",
                    tooltip="Consecutive identical frames. 'remove' drops them; 'detect only' counts them. "
                    "Runs after the stutter repair, on its result.",
                ),
                # This pack's extras.
                comfy_io.String.Input(
                    "video_url",
                    default="",
                    optional=True,
                    advanced=True,
                    tooltip="Public http(s) link to the source video (mp4, avi or mov), instead of connecting "
                    "a video. Nothing is uploaded to Comfy.org.",
                ),
                comfy_io.Boolean.Input(
                    "comparison",
                    default=True,
                    optional=True,
                    advanced=True,
                    tooltip="Also build a side-by-side video: the source on the left, the repaired video on "
                    "the right, in sync.",
                ),
            ],
            outputs=[
                comfy_io.Video.Output(tooltip="The repaired video; the source video when nothing was repaired."),
                comfy_io.Video.Output(
                    "comparison",
                    tooltip="Source (left) and repaired video (right) side by side. Nodes using it are skipped "
                    "when nothing was repaired or comparison is off.",
                ),
                comfy_io.Int.Output(
                    "inserted_frame_count",
                    tooltip="Frames the detector found missing for periodic stutter (not counting "
                    "insert_frame_indices). A detection count: the repair may have been skipped.",
                ),
                comfy_io.Int.Output("duplicate_frame_count", tooltip="Frames detected as repeats of the frame before."),
                comfy_io.String.Output("response", tooltip="The MediaKit task as JSON."),
            ],
            hidden=[
                comfy_io.Hidden.auth_token_comfy_org,
                comfy_io.Hidden.api_key_comfy_org,
                comfy_io.Hidden.unique_id,
            ],
        )

    @classmethod
    @with_default_client("mediakit_client", build_default_mediakit_client)
    async def execute(
        cls,
        mediakit_client,
        periodic_stutter,
        duplicate_frames,
        video=None,
        video_url="",
        comparison=True,
    ) -> comfy_io.NodeOutput:
        link = check_source(video, video_url)
        body = build_smoothness_request(link, periodic_stutter or {}, duplicate_frames)
        if video is not None:
            validate_smoothness_source(video, smoothness_repairs(body))
            link = body["video_url"] = await upload_source(cls, video)

        task = await submit_and_wait(
            mediakit_client, "/tools/enhance-video-smoothness", body, cls.hidden.unique_id, "smooth_task_submitted"
        )
        result = task_result(task)
        inserted = _detail_count(result, "inserted_frames_detail", "inserted_frame_count")
        duplicates = _detail_count(result, "duplicate_frames_detail", "duplicate_frame_count")
        log_msg("smooth_summary", inserted=inserted, duplicates=duplicates)
        send_node_text(cls.hidden.unique_id, plain_text("smooth_summary", inserted=inserted, duplicates=duplicates))

        repaired_url = result.get("video_url")
        if not repaired_url:
            # Detection only, nothing to fix, or the repair failed MediaKit's quality check.
            log_msg("smooth_no_repair")
            source = video if video is not None else VideoFromFile(await source_file(None, link, "smooth_source"))
            return comfy_io.NodeOutput(source, ExecutionBlocker(None), inserted, duplicates, task_response(task))

        repaired_path = await _download(repaired_url, "smooth_repaired")
        comparison_video = ExecutionBlocker(None)
        if comparison:
            comparison_path = await make_comparison(build_side_by_side, video, link, "smooth_source", repaired_path)
            if comparison_path:
                comparison_video = VideoFromFile(comparison_path)
        return comfy_io.NodeOutput(
            VideoFromFile(repaired_path), comparison_video, inserted, duplicates, task_response(task)
        )


# --------------------------------------------------------------------------
# Image Quality Enhance (synchronous)
# --------------------------------------------------------------------------

IMAGE_VERSIONS = ["standard", "professional", "max"]
IMAGE_GENERATIVE_MODES = ["generative_first", "fidelity_first"]
IMAGE_SIZE_MULTIPLE = "multiple"
IMAGE_SIZE_TARGET = "target size"
IMAGE_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
IMAGE_MAX_PARALLEL = 3
IMAGE_MAX_RECOMMENDED_SIDE = 8000  # max version: larger outputs can time out

# Per tool_version (AI MediaKit docs): input short/long side, long:short ratio,
# multiple, output short/long side, target_width/height range (None: the
# original size), and the final magnification cap of a target size.
IMAGE_VERSION_LIMITS = {
    "standard": {
        "in_short": (16, 1440), "in_long": (16, 2160), "max_ratio": None, "multiple": 8.0,
        "out_short": 6144, "out_long": 6144, "target": (None, 6144), "target_scale": 8.0,
        "in_rule": "16-1440 px on the short side and 16-2160 px on the long side",
        "out_rule": "at most 6144 px on each side",
    },
    "professional": {
        "in_short": (256, None), "in_long": (None, 2048), "max_ratio": None, "multiple": 30.0,
        "out_short": None, "out_long": 10240, "target": (64, 10240), "target_scale": None,
        "in_rule": "a short side of at least 256 px and a long side of at most 2048 px",
        "out_rule": "a long side of at most 10240 px",
    },
    "max": {
        "in_short": (64, None), "in_long": (None, 6240), "max_ratio": 32.0, "multiple": 30.0,
        "out_short": None, "out_long": 10240, "target": (64, 10240), "target_scale": None,
        "in_rule": "a short side of at least 64 px, a long side of at most 6240 px and a ratio up to 32:1",
        "out_rule": "a long side of at most 10240 px",
    },
}
IMAGE_UPLOAD_CACHE = {}
IMAGE_UPLOAD_CACHE_TTL_SECONDS = 12 * 3600  # Comfy.org links last about 24 h
IMAGE_UPLOAD_CACHE_MAX_ENTRIES = 64


def _image_tool_version_input():
    mode = comfy_io.Combo.Input(
        "generative_enhance_mode",
        options=IMAGE_GENERATIVE_MODES,
        default="generative_first",
        tooltip="'generative_first' adds richer, natural texture: for compressed, blurry or old images. "
        "'fidelity_first' keeps existing detail (faces, textures, text) as close to the original as "
        "possible: for portraits, product shots and screenshots.",
    )
    return comfy_io.DynamicCombo.Input(
        "tool_version",
        options=[
            comfy_io.DynamicCombo.Option("standard", []),
            comfy_io.DynamicCombo.Option("professional", [mode]),
            comfy_io.DynamicCombo.Option(
                "max",
                [
                    mode,
                    comfy_io.Boolean.Input(
                        "enable_correct_color",
                        default=True,
                        tooltip="Keep the colours close to the original. Turn off if the result looks "
                        "over-sharpened.",
                    ),
                ],
            ),
        ],
        tooltip="'standard' (up to 8x, PNG output): fast clean-up for thumbnails, UGC and OCR input. "
        "'professional' (up to 30x): fine texture for AIGC fixes, product and portrait photos, old photos. "
        "'max' (up to 30x): a generative model for the most detail on low-quality images. Pricing differs "
        "per version.",
    )


def _image_output_size_input():
    return comfy_io.DynamicCombo.Input(
        "output_size",
        options=[
            comfy_io.DynamicCombo.Option(
                IMAGE_SIZE_MULTIPLE,
                [
                    comfy_io.Float.Input(
                        "multiple",
                        default=2.0,
                        min=1.0,
                        max=30.0,
                        step=0.01,
                        tooltip="Scale factor for width and height: 1-8 for standard, 1-30 for professional "
                        "and max.",
                    ),
                ],
            ),
            comfy_io.DynamicCombo.Option(
                IMAGE_SIZE_TARGET,
                [
                    comfy_io.Int.Input(
                        "target_width",
                        default=1920,
                        min=0,
                        max=10240,
                        tooltip="Output width in px; 0 follows target_height and the aspect ratio. Standard: "
                        "from the original width to 6144 px (at most 8x); professional and max: 64-10240 px.",
                    ),
                    comfy_io.Int.Input(
                        "target_height",
                        default=0,
                        min=0,
                        max=10240,
                        tooltip="Output height in px; 0 follows target_width and the aspect ratio. With both "
                        "set, the image fits inside the box at its own aspect ratio.",
                    ),
                ],
            ),
        ],
        tooltip="Scale by a factor, or set a target width and/or height.",
    )


def build_image_enhance_request(image_url, tool_version, output_size):
    """The POST /tools-sync/enhance-image body; version options only for the versions that take them."""
    version = tool_version.get("tool_version", "standard")
    body = {"image_url": image_url, "tool_version": version}
    if version in ("professional", "max") and tool_version.get("generative_enhance_mode"):
        body["generative_enhance_mode"] = tool_version["generative_enhance_mode"]
    if version == "max":
        body["enable_correct_color"] = bool(tool_version.get("enable_correct_color", True))
    if output_size.get("output_size", IMAGE_SIZE_MULTIPLE) == IMAGE_SIZE_TARGET:
        for name in ("target_width", "target_height"):
            if int(output_size.get(name) or 0) > 0:
                body[name] = int(output_size[name])
    else:
        body["multiple"] = round(float(output_size.get("multiple", 2.0)), 2)
    return body


def _in_range(value, bounds):
    low, high = bounds
    return (low is None or value >= low) and (high is None or value <= high)


def plan_image_enhance(body, size=None):
    """
    Check the request against the version's documented limits and return the
    expected output size (None when the source size is unknown, as for
    image_url). Raises BytePlusException with the rule that is broken.
    """
    version = body["tool_version"]
    limits = IMAGE_VERSION_LIMITS[version]
    if "multiple" in body and not 1.0 <= body["multiple"] <= limits["multiple"]:
        raise BytePlusException(
            get_text("err_image_enhance_multiple", max=int(limits["multiple"]), version=version, value=body["multiple"])
        )
    targets = {name: body[name] for name in ("target_width", "target_height") if name in body}
    if "multiple" not in body and not targets:
        raise BytePlusException(get_text("err_image_enhance_target_missing"))
    if size is None:
        for name, value in targets.items():
            low, high = limits["target"]
            if not _in_range(value, (low, high)):
                raise BytePlusException(
                    get_text("err_image_enhance_target_range", name=name, min=low or 1, max=high,
                             version=version, value=value)
                )
        return None

    width, height = size
    short, long_ = min(width, height), max(width, height)
    ratio_ok = limits["max_ratio"] is None or long_ / float(short) <= limits["max_ratio"]
    if not (_in_range(short, limits["in_short"]) and _in_range(long_, limits["in_long"]) and ratio_ok):
        raise BytePlusException(
            get_text("err_image_enhance_input_size", version=version, rule=limits["in_rule"], width=width, height=height)
        )
    if "multiple" in body:
        scale = body["multiple"]
    else:
        for name, value in targets.items():
            original = width if name == "target_width" else height
            low, high = limits["target"]
            low = original if low is None else low
            if not _in_range(value, (low, high)):
                raise BytePlusException(
                    get_text("err_image_enhance_target_range", name=name, min=low, max=high,
                             version=version, value=value)
                )
        scale = min(
            targets.get("target_width", float("inf")) / float(width),
            targets.get("target_height", float("inf")) / float(height),
        )
        if limits["target_scale"] and scale > limits["target_scale"] + 1e-9:
            raise BytePlusException(
                get_text("err_image_enhance_multiple", max=int(limits["target_scale"]), version=version,
                         value=round(scale, 2))
            )
    out_w, out_h = int(round(width * scale)), int(round(height * scale))
    if (limits["out_long"] and max(out_w, out_h) > limits["out_long"]) or (
        limits["out_short"] and min(out_w, out_h) > limits["out_short"]
    ):
        raise BytePlusException(
            get_text("err_image_enhance_output_size", version=version, width=out_w, height=out_h,
                     rule=limits["out_rule"])
        )
    return out_w, out_h


def encode_image_for_upload(image, max_bytes=IMAGE_MAX_UPLOAD_BYTES):
    """
    (bytes, filename, mime type) for one [H, W, C] image: PNG, or JPEG when
    the PNG is over MediaKit's 10 MB limit. Professional and max keep the
    input format in their output.
    """
    import numpy
    from PIL import Image

    array = numpy.clip(image[..., :3].cpu().numpy() * 255.0, 0, 255).astype(numpy.uint8)
    picture = Image.fromarray(array, "RGB")
    with io.BytesIO() as buffer:
        picture.save(buffer, format="PNG", compress_level=6)
        data = buffer.getvalue()
    if len(data) <= max_bytes:
        return data, "image.png", "image/png"
    for quality in (95, 90, 85, 80):
        with io.BytesIO() as buffer:
            picture.save(buffer, format="JPEG", quality=quality)
            data = buffer.getvalue()
        if len(data) <= max_bytes:
            return data, "image.jpg", "image/jpeg"
    raise BytePlusException(get_text("err_image_enhance_too_big", size=len(data)))


async def upload_image_source(node_cls, data, filename, mime_type):
    """Comfy.org storage URL for image bytes, reused for the same bytes."""
    return await upload_bytes_to_comfy_storage(
        node_cls,
        data,
        filename,
        mime_type,
        IMAGE_UPLOAD_CACHE,
        cache_key=hashlib.sha256(data).hexdigest(),
        ttl_seconds=IMAGE_UPLOAD_CACHE_TTL_SECONDS,
        max_entries=IMAGE_UPLOAD_CACHE_MAX_ENTRIES,
        unavailable_key="err_comfy_image_upload_unavailable_mediakit",
        failed_key="err_comfy_image_upload_failed_mediakit",
    )


def _load_image(path, remove=False):
    """[1, H, W, 3] float tensor from an image file (deleted afterwards when remove)."""
    import numpy
    from PIL import Image, ImageOps

    try:
        with Image.open(path) as picture:
            rgb = ImageOps.exif_transpose(picture).convert("RGB")
            array = numpy.asarray(rgb, dtype=numpy.float32) / 255.0
    finally:
        if remove:
            _remove_quietly(path)
    return torch.from_numpy(array).unsqueeze(0)


def resize_like(image, height, width):
    """[1, H, W, C] image resized (bicubic) to height x width, for side-by-side comparison."""
    resized = torch.nn.functional.interpolate(
        image.movedim(-1, 1), size=(height, width), mode="bicubic", align_corners=False, antialias=True
    )
    return resized.movedim(1, -1).clamp(0.0, 1.0)


class BytePlusImageEnhance(comfy_io.ComfyNode):
    """AI MediaKit Image Quality Enhancement: upscales and restores images in one synchronous call."""

    NODE_ID = "BytePlusImageEnhance"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id=cls.NODE_ID,
            display_name="BytePlus Image Quality Enhance",
            category=MEDIAKIT_CATEGORY,
            description="Upscales and restores images: super-resolution, artifact and noise removal, deblurring, "
            "sharpening, portrait, text and colour enhancement, picked per image. For AIGC post-processing, "
            "old photos, OCR input and product images.",
            inputs=[
                BytePlusMediaKitClientType.Input("mediakit_client", optional=True, tooltip=MEDIAKIT_CLIENT_TOOLTIP),
                comfy_io.Image.Input(
                    "image",
                    tooltip="Image(s) to enhance; each image in a batch is enhanced separately. Uploaded to "
                    "Comfy.org storage first (needs a Comfy.org login); or set image_url instead.",
                    optional=True,
                ),
                _image_tool_version_input(),
                _image_output_size_input(),
                # This pack's extras.
                comfy_io.String.Input(
                    "image_url",
                    default="",
                    optional=True,
                    advanced=True,
                    tooltip="Public http(s) link to the image (png, jpg, jpeg or webp, up to 10 MB), instead of "
                    "connecting an image. Nothing is uploaded to Comfy.org.",
                ),
            ],
            outputs=[
                comfy_io.Image.Output(tooltip="The enhanced image(s)."),
                comfy_io.Image.Output(
                    "original",
                    tooltip="The input resized to the enhanced size; connect it and the enhanced image to "
                    "Compare Images for a slider.",
                ),
                comfy_io.String.Output("response", tooltip="The MediaKit result as JSON (a list for a batch)."),
            ],
            hidden=[
                comfy_io.Hidden.auth_token_comfy_org,
                comfy_io.Hidden.api_key_comfy_org,
                comfy_io.Hidden.unique_id,
            ],
        )

    @classmethod
    @with_default_client("mediakit_client", build_default_mediakit_client)
    async def execute(cls, mediakit_client, tool_version, output_size, image=None, image_url="") -> comfy_io.NodeOutput:
        link = check_source(
            image,
            image_url,
            name="image_url",
            both_key="err_mediakit_image_source_both",
            missing_key="err_mediakit_image_source_missing",
        )
        template = build_image_enhance_request(link, tool_version or {}, output_size or {})
        version = template["tool_version"]
        if image is not None:
            sources = [image[i:i + 1] for i in range(image.shape[0])]
            plans = [plan_image_enhance(template, (int(s.shape[2]), int(s.shape[1]))) for s in sources]
        else:
            sources, plans = [None], [plan_image_enhance(template)]
        for plan in plans:
            if version == "max" and plan and max(plan) > IMAGE_MAX_RECOMMENDED_SIDE:
                log_msg("image_enhance_max_slow", width=plan[0], height=plan[1])

        semaphore = asyncio.Semaphore(IMAGE_MAX_PARALLEL)

        async def enhance(index, source):
            async with semaphore:
                body = dict(template)
                if source is not None:
                    encoded = await asyncio.to_thread(encode_image_for_upload, source[0])
                    body["image_url"] = await upload_image_source(cls, *encoded)
                send_node_text(
                    cls.hidden.unique_id,
                    plain_text("image_enhance_submitted", version=version, index=index + 1, count=len(sources)),
                )
                response = await mediakit_request(
                    mediakit_client, "POST", "/tools-sync/enhance-image", body,
                    timeout_seconds=MEDIAKIT_SYNC_TIMEOUT_SECONDS,
                )
                result = task_result(response)
                if not result.get("image_url"):
                    raise BytePlusException(get_text("err_mediakit_unexpected", status="no result image_url"))
                log_msg(
                    "image_enhance_done",
                    width=result.get("image_width", "?"),
                    height=result.get("image_height", "?"),
                    format=result.get("image_format", "?"),
                    task_id=response.get("task_id", ""),
                )
                extension = str(result.get("image_format") or "png").lower().replace("jpeg", "jpg")
                enhanced = await asyncio.to_thread(
                    _load_image, await _download(result["image_url"], "image_enhanced", extension), True
                )
                if source is None:
                    source = await asyncio.to_thread(_load_image, await _download(link, "image_source", "img"), True)
                return enhanced, source, {k: v for k, v in response.items() if k != "request_id"}

        done = await gather_cancelling([enhance(i, s) for i, s in enumerate(sources)])
        enhanced = [item[0] for item in done]
        height, width = enhanced[0].shape[1:3]
        # A batch is one tensor: results of another size are resized to the first one.
        enhanced = [e if e.shape[1:3] == (height, width) else resize_like(e, height, width) for e in enhanced]
        originals = [resize_like(item[1], height, width) for item in done]
        responses = [item[2] for item in done]
        return comfy_io.NodeOutput(
            torch.cat(enhanced),
            torch.cat(originals),
            json.dumps(responses[0] if len(responses) == 1 else responses, ensure_ascii=False),
        )


# Registered in __init__.py.
NODES = [BytePlusMediaKitClient, BytePlusVideoEnhance, BytePlusVideoSmoothness, BytePlusImageEnhance]
