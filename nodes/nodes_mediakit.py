"""
BytePlus VOD AI MediaKit nodes: the MediaKit Client (its own API key) and
vCube Video Enhance, shaped like ComfyUI core's ByteDanceVideoEnhanceNode
(comfy_api_nodes/nodes_bytedance.py).

Core calls MediaKit through the Comfy.org proxy; these nodes call
https://mediakit.<region>.bytepluses.com/api/v1 directly with a MediaKit API key
(Authorization: Bearer), which is a different key from the ModelArk and Seed
Speech keys. Enhancement is an asynchronous task: POST /tools/enhance-video,
then poll GET /tasks/{task_id} until completed.

This pack adds a before/after comparison: a video whose divider sweeps across
the frame, and a matching pair of frames for ComfyUI's Compare Images slider.
"""
import asyncio
import json
import math
import os
import time
import uuid

import aiohttp
import comfy.model_management
import folder_paths
import torch
from comfy_api.input_impl import VideoFromFile
from comfy_api.latest import io as comfy_io

from .constants import (
    DEFAULT_MEDIAKIT_REGION,
    MEDIAKIT_API_KEY_ENV,
    MEDIAKIT_API_KEYS_CONSOLE_URL,
    MEDIAKIT_MAX_POLL_ERRORS,
    MEDIAKIT_POLL_SECONDS,
    MEDIAKIT_REGION_BASE_URLS,
    MEDIAKIT_REQUEST_TIMEOUT_SECONDS,
)
from .nodes_shared import (
    GLOBAL_CATEGORY,
    LOG_PREFIX,
    ApiKeyStore,
    BytePlusException,
    _notify_api_key_saved,
    get_text,
    log_msg,
)

MEDIAKIT_CATEGORY = f"{GLOBAL_CATEGORY}/MediaKit"
ENV_KEY_OPTION = f"Environment ({MEDIAKIT_API_KEY_ENV})"

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
VCUBE_MAX_BITRATE_KBPS = 150000

# Comparison video: short side at most 1080 px, divider sweep period.
COMPARISON_MAX_SHORT_SIDE = 1080
COMPARISON_SWEEP_SECONDS = 4.0


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


def _plain(key, **kwargs):
    text = get_text(key, **kwargs)
    return text[len(LOG_PREFIX):] if text.startswith(LOG_PREFIX) else text


def describe_mediakit_error(error, status=None, request_id=None):
    """A readable message from MediaKit's error object (code, message, param)."""
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code") or (f"HTTP {status}" if status else "Error"))
    message = str(error.get("message") or "")
    param = error.get("param")
    hint = ""
    if code in ("Unauthorized", "InvalidApiKey", "AuthenticationFailed") or status == 401:
        hint = _plain("mediakit_hint_auth", url=MEDIAKIT_API_KEYS_CONSOLE_URL)
    return get_text(
        "err_mediakit_api",
        code=code,
        message=message,
        param=f" (param: {param})" if param else "",
        request_id=f" Request ID: {request_id}." if request_id else "",
        hint=hint,
    )


async def _send(client, method, path, body=None):
    """One HTTP call to MediaKit; returns (status, parsed JSON or None). Tests replace this."""
    timeout = aiohttp.ClientTimeout(total=MEDIAKIT_REQUEST_TIMEOUT_SECONDS)
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


async def mediakit_request(client, method, path, body=None):
    """Call MediaKit; raise BytePlusException unless the response reports success."""
    try:
        status, data = await _send(client, method, path, body)
    except comfy.model_management.InterruptProcessingException:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise BytePlusException(get_text("err_mediakit_network", e=e))
    if not isinstance(data, dict):
        raise BytePlusException(get_text("err_mediakit_unexpected", status=status))
    if status >= 400 or data.get("success") is False:
        raise BytePlusException(describe_mediakit_error(data.get("error"), status, data.get("request_id")))
    return data


async def _sleep_interruptibly(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        comfy.model_management.throw_exception_if_processing_interrupted()
        await asyncio.sleep(min(0.5, max(0.0, deadline - time.monotonic())))


def _send_progress_text(node_id, text):
    try:
        from server import PromptServer

        PromptServer.instance.send_progress_text(text, node_id)
    except Exception:
        pass


async def wait_for_mediakit_task(client, task_id, node_id=None, poll_seconds=MEDIAKIT_POLL_SECONDS):
    """
    Poll GET /tasks/{task_id} until completed (returns the task) or failed
    (raises). No client-side time limit: Professional runs can take hours;
    the wait is interruptible.
    """
    started = time.monotonic()
    errors = 0
    while True:
        comfy.model_management.throw_exception_if_processing_interrupted()
        try:
            task = await mediakit_request(client, "GET", f"/tasks/{task_id}")
            errors = 0
        except BytePlusException:
            errors += 1
            if errors >= MEDIAKIT_MAX_POLL_ERRORS:
                raise
            await _sleep_interruptibly(poll_seconds)
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
        _send_progress_text(
            node_id,
            _plain("mediakit_task_waiting", task_id=task_id, status=status or "running", elapsed=elapsed),
        )
        await _sleep_interruptibly(poll_seconds)


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
    ModelArk or Seed Speech key) for vCube Video Enhance.
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
                "BytePlus VOD AI MediaKit API key for vCube Video Enhance. Create it on the AI MediaKit "
                "Settings page of the VOD console; ModelArk and Seed Speech keys do not work here."
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
            api_key = os.environ.get(MEDIAKIT_API_KEY_ENV, "").strip()
            if not api_key:
                raise BytePlusException(get_text("mediakit_env_key_missing", env=MEDIAKIT_API_KEY_ENV))
        else:
            MEDIAKIT_API_KEY_STORE.load()
            api_key = MEDIAKIT_API_KEY_STORE.find_api_key(key_name)
            if not api_key:
                raise BytePlusException(get_text("mediakit_key_not_found", key_name=key_name))
        return comfy_io.NodeOutput(MediaKitClient(api_key, region))


# --------------------------------------------------------------------------
# vCube Video Enhance
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


async def _download(url, prefix):
    from .utils_download import _download_to_file_stream_async

    path = _temp_path(prefix)
    timeout = aiohttp.ClientTimeout(total=None, sock_read=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        ok = await _download_to_file_stream_async(session, url, path)
    if not ok or not os.path.exists(path):
        raise BytePlusException(get_text("err_mediakit_download_failed", url=url.split("?", 1)[0]))
    return path


# --- Comparison --------------------------------------------------------------

def _even(value):
    value = max(2, int(round(value)))
    return value - value % 2


def comparison_size(width, height, max_short_side=COMPARISON_MAX_SHORT_SIDE):
    """The enhanced video's size, scaled down so its short side is at most max_short_side."""
    scale = min(1.0, float(max_short_side) / float(min(width, height)))
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


def build_comparison(source_path, enhanced_path, compare_time=-1.0):
    """
    (comparison video path, source frame tensor, enhanced frame tensor).
    The video follows the enhanced clip's timing at a short side of at most
    1080 px; the frames are full enhanced resolution, the source scaled to match.
    """
    import av
    import numpy

    with av.open(enhanced_path) as enhanced, av.open(source_path) as source:
        e_stream = enhanced.streams.video[0]
        s_stream = source.streams.video[0]
        width, height = e_stream.codec_context.width, e_stream.codec_context.height
        duration = float(e_stream.duration * e_stream.time_base) if e_stream.duration else None
        if duration is None and enhanced.duration:
            duration = enhanced.duration / av.time_base
        rate = e_stream.average_rate or e_stream.guessed_rate or 30

        # Still pair for Compare Images.
        t = (duration or 0.0) / 2.0 if compare_time is None or compare_time < 0 else float(compare_time)
        e_frame = _frame_at(enhanced, e_stream, t)
        s_frame = _frame_at(source, s_stream, t)
        if e_frame is None or s_frame is None:
            raise BytePlusException(get_text("err_vcube_comparison_failed", e="no frames decoded"))
        enhanced_still = e_frame.to_ndarray(format="rgb24")
        source_still = s_frame.reformat(width=width, height=height, format="rgb24").to_ndarray()

        # Sweeping comparison video.
        out_w, out_h = comparison_size(width, height)
        out_path = _temp_path("vcube_comparison")
        enhanced.seek(0)
        source.seek(0)
        source_frames = source.decode(s_stream)
        current = next(source_frames, None)
        upcoming = next(source_frames, None)
        with av.open(out_path, mode="w") as output:
            out_stream = output.add_stream("libx264", rate=rate)
            out_stream.width, out_stream.height = out_w, out_h
            out_stream.pix_fmt = "yuv420p"
            out_stream.options = {"crf": "18", "preset": "veryfast"}
            for frame in enhanced.decode(e_stream):
                comfy.model_management.throw_exception_if_processing_interrupted()
                ft = float(frame.time or 0.0)
                # Advance the source to the frame shown at time ft.
                while upcoming is not None and upcoming.time is not None and upcoming.time <= ft:
                    current, upcoming = upcoming, next(source_frames, None)
                if current is None:
                    break
                after = frame.reformat(width=out_w, height=out_h, format="rgb24").to_ndarray()
                before = current.reformat(width=out_w, height=out_h, format="rgb24").to_ndarray()
                composed = _draw_labels(compose_comparison_frame(before, after, sweep_position(ft)))
                for packet in out_stream.encode(av.VideoFrame.from_ndarray(composed, format="rgb24")):
                    output.mux(packet)
            for packet in out_stream.encode():
                output.mux(packet)

    def to_tensor(array):
        return torch.from_numpy(numpy.ascontiguousarray(array)).float().div(255.0).unsqueeze(0)

    return out_path, to_tensor(source_still), to_tensor(enhanced_still)


class BytePlusVideoEnhance(comfy_io.ComfyNode):
    """Core's ByteDanceVideoEnhanceNode (vCube) on BytePlus VOD AI MediaKit."""

    NODE_ID = "BytePlusVideoEnhance"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id=cls.NODE_ID,
            display_name="BytePlus vCube Video Enhance",
            category=MEDIAKIT_CATEGORY,
            description="Upscales and restores a video with ByteDance vCube: super-resolution up to 8K, "
            "compression artifact and noise removal, colour and sharpness enhancement, "
            "optional frame interpolation.",
            inputs=[
                BytePlusMediaKitClientType.Input("mediakit_client"),
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
                    tooltip="Before (left) and after (right) with a sweeping divider. Empty when comparison is off.",
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
            is_output_node=True,
        )

    @classmethod
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
        link = str(video_url or "").strip()
        if video is not None and link:
            raise BytePlusException(get_text("err_vcube_source_both"))
        if video is None and not link:
            raise BytePlusException(get_text("err_vcube_source_missing"))
        if link and not link.lower().startswith(("http://", "https://")):
            raise BytePlusException(get_text("err_vcube_url_invalid", url=link))

        source_size = source_fps = None
        if video is not None:
            source_size = validate_source_video(video)
            try:
                source_fps = float(video.get_frame_rate())
            except Exception:
                source_fps = None
            from .nodes_video import upload_videos_to_comfy_storage_cached

            link = (
                await upload_videos_to_comfy_storage_cached(
                    cls,
                    [video],
                    unavailable_key="err_comfy_video_upload_unavailable_vcube",
                    failed_key="err_comfy_video_upload_failed_vcube",
                )
            )[0]

        body = build_enhance_request(
            link, tool_version or {}, resolution or {}, fps, bitrate_level, bitrate, source_size, source_fps
        )
        created = await mediakit_request(mediakit_client, "POST", "/tools/enhance-video", body)
        task_id = created.get("task_id")
        if not task_id:
            raise BytePlusException(get_text("err_mediakit_unexpected", status="no task_id"))
        log_msg("vcube_task_submitted", task_id=task_id)
        task = await wait_for_mediakit_task(mediakit_client, task_id, cls.hidden.unique_id)
        result_url = task_result(task).get("video_url")
        if not result_url:
            raise BytePlusException(get_text("err_mediakit_unexpected", status="completed without video_url"))
        enhanced_path = await _download(result_url, "vcube_enhanced")
        response = json.dumps({k: v for k, v in task.items() if k != "request_id"}, ensure_ascii=False)

        comparison_video = source_frame = enhanced_frame = None
        if comparison:
            if video is not None:
                source_path = _temp_path("vcube_source")
                await asyncio.to_thread(video.save_to, source_path)
            else:
                source_path = await _download(link, "vcube_source")
            try:
                comparison_path, source_frame, enhanced_frame = await asyncio.to_thread(
                    build_comparison, source_path, enhanced_path, compare_time
                )
            except comfy.model_management.InterruptProcessingException:
                raise
            except BytePlusException:
                raise
            except Exception as e:
                raise BytePlusException(get_text("err_vcube_comparison_failed", e=e))
            comparison_video = VideoFromFile(comparison_path)

        return comfy_io.NodeOutput(
            VideoFromFile(enhanced_path), comparison_video, source_frame, enhanced_frame, response
        )


# Registered in __init__.py.
NODES = [BytePlusMediaKitClient, BytePlusVideoEnhance]
