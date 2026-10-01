"""
Seedance 2 / 2.5 nodes shaped like ComfyUI core's ByteDance2* nodes: Text to Video, First-Last-Frame to Video, Reference to Video, Draft to Final Video.

The inputs, defaults, tooltips and outputs copy core's nodes
(comfy_api_nodes/nodes_bytedance.py); each node also takes this pack's API
Client first and this pack's extras (advanced) after core's inputs. Requests go
straight to BytePlus ModelArk with the user's key through the shared video
executor. Local images and audio are sent inline as base64; local reference
videos are uploaded to Comfy.org storage (needs a Comfy.org login).
"""
import asyncio
import json
import math
import re
import time
from io import BytesIO

import comfy.model_management
from comfy_api.latest import io as comfy_io

from .constants import (
    DEFAULT_FILENAME_PREFIX,
    IMAGE_MAX_EDGE,
    IMAGE_MAX_RATIO,
    IMAGE_MIN_EDGE,
    IMAGE_MIN_RATIO,
    REF_AUDIO_MAX_TOTAL_REQUEST_MB,
    REF_IMAGE_MAX_TOTAL_REQUEST_MB,
    REF_MEDIA_MAX_DURATION,
    REF_MEDIA_MAX_DURATION_SEEDANCE_2_5,
    REF_MEDIA_MIN_DURATION,
    REF_VIDEO_MAX_FPS,
    REF_VIDEO_MAX_PIXELS,
    REF_VIDEO_MAX_SIZE_MB,
    REF_VIDEO_MIN_FPS,
    REF_VIDEO_MIN_PIXELS,
    SEEDANCE_2_5_EDIT_MIN_DURATION,
)
from .core_style import (
    last_frame_batch_output,
    raise_if_output_linked,
    resolve_reference_values,
    resolve_typed_reference,
    seed_input,
    video_extra_inputs,
    video_list_output,
    watermark_input,
)
from .models_config import (
    SEEDANCE2_CORE_DRAFT_OPTIONS,
    SEEDANCE2_CORE_MODEL_OPTIONS,
    SEEDANCE2_REF_VIDEO_DOWNSCALE_TARGETS,
    SEEDANCE_2_5_FAMILY,
    SEEDANCE_2_5_TASK_TYPES,
    SEEDANCE_2_5_OUTPUT_FORMATS,
    SEEDANCE_DRAFT_FINAL_RESOLUTIONS,
    SEEDANCE_DRAFT_RESOLUTION,
    VIDEO_2_MODEL_MAX_DURATIONS,
    VIDEO_2_MODEL_REFERENCE_LIMITS,
    VIDEO_2_MODEL_RESOLUTIONS,
    VIDEO_MODEL_MAP,
)
from .nodes_shared import (
    GLOBAL_CATEGORY,
    LOG_PREFIX,
    BytePlusClientType,
    BytePlusException,
    format_api_error,
    get_node_count_in_workflow,
    get_text,
    log_msg,
    video_source_size_bytes,
)
from .nodes_video import (
    BytePlusVideoBase,
    _parse_draft_task_ids,
    build_draft_final_content,
    upload_videos_to_comfy_storage_cached,
    validate_seedance2_duration,
    validate_seedance2_resolution,
)
from .nodes_video_schema import resolve_model_id

SEEDANCE_MODEL_TOOLTIP = (
    "Seedance 2.5 for the newest model, videos up to 30 seconds and mp4/mov output; "
    "Seedance 2.5 Draft for a quick 480p preview whose draft_task_id renders the 1080p final "
    "in the BytePlus Seedance 2.5 Draft to Final Video node; "
    "Seedance 2.5 Premium for 4k output, and Seedance 2.5 Premium Draft for its 480p preview "
    "(rendered in 4k); "
    "Seedance 2.0 for maximum quality and 4k; Fast for speed optimization; "
    "Mini for the fastest, lowest-cost generation."
)
DRAFT_TASK_ID_OUTPUT_TOOLTIP = (
    "Task ID of a Seedance 2.5 Draft or Seedance 2.5 Premium Draft run. Connect it to the "
    "BytePlus Seedance 2.5 Draft to Final Video node to render the final (1080p, or 4k for Premium). "
    "Several drafts (generation_count above 1) give one ID per line."
)
RATIO_OPTIONS = ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive"]
FORBIDDEN_PROMPT_PARAMS = ["resolution", "ratio", "dur", "frames", "seed", "generate_audio"]
TASK_TYPE_TOOLTIP = (
    "What to do with the reference media. Every value except auto is "
    "validated when the task is submitted, so mismatched settings fail before "
    "generation starts. auto: the model infers the task from the prompt and "
    "inputs, and settings that conflict with its reading fail only after "
    "generation has started. reference: generate a new video guided by the "
    "reference images, videos, and audio. edit: change a connected reference "
    "video (add, remove, replace); the output keeps the source clip's own length "
    "and aspect ratio, and the duration and ratio widgets are ignored. extend: "
    "continue a connected reference video forward or backward; the prompt should "
    "say 'extend forward', 'extend backward', or 'continue', the aspect ratio "
    "follows the source clip, and the output contains only the newly generated "
    "segment of the duration you set, not the source clip."
)
AUTO_DOWNSCALE_TOOLTIP = (
    "Automatically downscale reference videos that exceed the model's pixel budget "
    "for the selected resolution. Aspect ratio is preserved; videos already within limits are untouched."
)
AUTO_UPSCALE_TOOLTIP = (
    "Automatically upscale reference videos that are below the model's minimum pixel count "
    "for the selected resolution. Aspect ratio is preserved; videos already meeting the minimum are "
    "untouched. Note: upscaling a low-resolution source does not add real detail and may produce "
    "lower-quality generations."
)
REFERENCE_ASSETS_TOOLTIP = (
    "Asset IDs (or asset://<asset_id>) from your private asset library, or https:// links to "
    "image, video or audio files. Up to 50 references in total on Seedance 2.5 (30 images, 10 videos, "
    "10 audio clips, counting connected inputs) and 15 on Seedance 2.0 (9 + 3 + 3). Refer to a slot in "
    "the prompt as assetN (for example asset1); it is rewritten to the matching Image / Video / Audio label."
)

# Supported output aspect ratios, used to pre-size FLF frames to the exact
# output pixel size (avoids the 1080p stretch jump, as in core).
SEEDANCE2_RATIO_WH = {
    "16:9": (16, 9),
    "4:3": (4, 3),
    "1:1": (1, 1),
    "3:4": (3, 4),
    "9:16": (9, 16),
    "21:9": (21, 9),
}
SEEDANCE2_RES_SHORT_SIDE = {"480p": 480, "720p": 720, "1080p": 1080, "4k": 2160}

_ASSET_REF_RE = re.compile(r"\basset ?(\d{1,2})\b", re.IGNORECASE)


# --------------------------------------------------------------------------
# Schema helpers (copies of core's _seedance2_text_inputs & co.)
# --------------------------------------------------------------------------

def _model_key(label):
    return SEEDANCE2_CORE_MODEL_OPTIONS[label]


def _is_2_5_family(label):
    return _model_key(label) in SEEDANCE_2_5_FAMILY


def _is_draft(label):
    return label in SEEDANCE2_CORE_DRAFT_OPTIONS


def _option_resolutions(label):
    """(options, default) of the resolution widget for a model option."""
    if _is_draft(label):
        return [SEEDANCE_DRAFT_RESOLUTION], SEEDANCE_DRAFT_RESOLUTION
    resolutions = list(VIDEO_2_MODEL_RESOLUTIONS[_model_key(label)])
    if not _is_2_5_family(label):
        return resolutions, None  # core: no default, the first option
    return resolutions, "720p" if "720p" in resolutions else resolutions[0]


def _seedance2_text_inputs(label, default_ratio="16:9"):
    resolutions, _default = _option_resolutions(label)
    max_duration = VIDEO_2_MODEL_MAX_DURATIONS[_model_key(label)]
    return [
        comfy_io.String.Input(
            "prompt",
            multiline=True,
            default="",
            tooltip="Text prompt for video generation.",
        ),
        comfy_io.Combo.Input(
            "resolution",
            options=resolutions,
            tooltip="Resolution of the output video.",
        ),
        comfy_io.Combo.Input(
            "ratio",
            options=list(RATIO_OPTIONS),
            default=default_ratio,
            tooltip="Aspect ratio of the output video.",
        ),
        comfy_io.Int.Input(
            "duration",
            default=7,
            min=4,
            max=max_duration,
            step=1,
            tooltip=f"Duration of the output video in seconds (4-{max_duration}).",
            display_mode=comfy_io.NumberDisplay.slider,
        ),
        comfy_io.Boolean.Input(
            "generate_audio",
            default=True,
            tooltip="Enable audio generation for the output video.",
        ),
    ]


def _seedance25_text_inputs(label, with_ratio=True, with_task_type=False):
    resolutions, default_resolution = _option_resolutions(label)
    max_duration = VIDEO_2_MODEL_MAX_DURATIONS[_model_key(label)]
    inputs = [
        comfy_io.String.Input(
            "prompt",
            multiline=True,
            default="",
            tooltip="Text prompt for video generation. Put spoken lines in double quotes to steer "
            "the generated dialogue.",
        ),
        comfy_io.Combo.Input(
            "resolution",
            options=resolutions,
            default=default_resolution,
            tooltip="Resolution of the output video.",
        ),
    ]
    if with_ratio:
        inputs.append(
            comfy_io.Combo.Input(
                "ratio",
                options=list(RATIO_OPTIONS),
                default="16:9",
                tooltip="Aspect ratio of the output video.",
            )
        )
    inputs += [
        comfy_io.Int.Input(
            "duration",
            default=5,
            min=4,
            max=max_duration,
            step=1,
            tooltip=f"Duration of the output video in seconds (4-{max_duration}).",
            display_mode=comfy_io.NumberDisplay.slider,
        ),
        comfy_io.Boolean.Input(
            "generate_audio",
            default=True,
            tooltip="Enable audio generation for the output video.",
        ),
    ]
    if with_task_type:
        inputs.append(
            comfy_io.Combo.Input(
                "task_type",
                options=list(SEEDANCE_2_5_TASK_TYPES),
                default="auto",
                tooltip=TASK_TYPE_TOOLTIP,
            )
        )
    inputs.append(
        comfy_io.Combo.Input(
            "output_format",
            options=list(SEEDANCE_2_5_OUTPUT_FORMATS),
            default="mp4",
            tooltip="Container format of the output video.",
        )
    )
    return inputs


def _text_option_inputs(label, with_ratio=True, default_ratio="16:9", with_task_type=False):
    if _is_2_5_family(label):
        return _seedance25_text_inputs(label, with_ratio=with_ratio, with_task_type=with_task_type)
    return _seedance2_text_inputs(label, default_ratio=default_ratio)


def _named_autogrow(input_id, template, prefix, count, tooltip=None):
    return comfy_io.Autogrow.Input(
        input_id,
        template=comfy_io.Autogrow.TemplateNames(
            template,
            names=[f"{prefix}_{i}" for i in range(1, count + 1)],
            min=0,
        ),
        tooltip=tooltip,
    )


def _reference_option_inputs(label):
    limits = VIDEO_2_MODEL_REFERENCE_LIMITS[_model_key(label)]
    if _is_2_5_family(label):
        text_inputs = _seedance25_text_inputs(label, with_task_type=True)
    else:
        text_inputs = _seedance2_text_inputs(label, default_ratio="adaptive")
    return [
        *text_inputs,
        _named_autogrow("reference_images", comfy_io.Image.Input("reference_image"), "image", limits["images"]),
        _named_autogrow("reference_videos", comfy_io.Video.Input("reference_video"), "video", limits["videos"]),
        _named_autogrow("reference_audios", comfy_io.Audio.Input("reference_audio"), "audio", limits["audios"]),
        comfy_io.Boolean.Input(
            "auto_downscale",
            default=True,
            optional=True,
            tooltip=AUTO_DOWNSCALE_TOOLTIP,
        ),
        comfy_io.Boolean.Input(
            "auto_upscale",
            default=False,
            advanced=True,
            optional=True,
            tooltip=AUTO_UPSCALE_TOOLTIP,
        ),
        # Core stops at the image cap (9 / 30), but assets can be images, videos
        # or audio: BytePlus allows 15 references in total on 2.0 and 50 on 2.5.
        _named_autogrow(
            "reference_assets",
            comfy_io.String.Input("reference_asset"),
            "asset",
            limits["images"] + limits["videos"] + limits["audios"],
            tooltip=REFERENCE_ASSETS_TOOLTIP,
        ),
    ]


def _model_input(option_inputs):
    return comfy_io.DynamicCombo.Input(
        "model",
        options=[
            comfy_io.DynamicCombo.Option(label, option_inputs(label))
            for label in SEEDANCE2_CORE_MODEL_OPTIONS
        ],
        tooltip=SEEDANCE_MODEL_TOOLTIP,
    )


def _generation_outputs():
    return [
        video_list_output(),
        comfy_io.String.Output("draft_task_id", tooltip=DRAFT_TASK_ID_OUTPUT_TOOLTIP),
        last_frame_batch_output(),
        comfy_io.String.Output("response", tooltip="Task responses as JSON."),
    ]


# --------------------------------------------------------------------------
# Request helpers
# --------------------------------------------------------------------------

def _plain_text(key, **kwargs):
    """A message without the console prefix, for use inside another message."""
    text = get_text(key, **kwargs)
    return text[len(LOG_PREFIX):] if text.startswith(LOG_PREFIX) else text


def _selected_label(model):
    label = model.get("model") if isinstance(model, dict) else None
    if label not in SEEDANCE2_CORE_MODEL_OPTIONS:
        raise BytePlusException(get_text("err_model_not_supported", model=label))
    return label


def _require_prompt(model):
    prompt = str(model.get("prompt") or "")
    if not prompt.strip():
        raise BytePlusException(get_text("err_seedance2_prompt_empty"))
    return prompt


def validate_draft_output(cls, label):
    """Core's _seedance2_validate_draft_output: draft_task_id (output 1) needs a Draft model."""
    if not _is_draft(label):
        raise_if_output_linked(cls, 1, _plain_text("err_seedance2_draft_output_linked"))


def _slot_number(key):
    try:
        return int(str(key).rsplit("_", 1)[-1])
    except ValueError:
        return 10**6


def _ordered_values(values):
    """Autogrow dict (connected slots only) -> values in slot order."""
    if not isinstance(values, dict):
        return []
    return [values[key] for key in sorted(values, key=_slot_number) if values[key] is not None]


def task_ids_from_response(response_text):
    """Newline-joined task IDs from the executor's response JSON."""
    try:
        data = json.loads(response_text or "")
    except (TypeError, ValueError):
        return ""
    if isinstance(data, dict):
        ids = data.get("task_ids") or []
    elif isinstance(data, list):
        ids = [item.get("id") for item in data if isinstance(item, dict)]
    else:
        ids = []
    return "\n".join(str(task_id) for task_id in ids if task_id)


def build_seedance2_request_params(model, label, ratio, watermark):
    """
    Core's _seedance2_build_request as (duration, auto_duration, ratio,
    extra_api_params) for the shared executor. edit -> adaptive ratio and
    duration -1; extend -> adaptive ratio.
    """
    task_type = model.get("task_type", "auto") or "auto"
    auto_duration = False
    if task_type == "edit":
        ratio, auto_duration = "adaptive", True
    elif task_type == "extend":
        ratio = "adaptive"
    extra = {
        "generate_audio": bool(model.get("generate_audio", True)),
        "watermark": bool(watermark),
    }
    if model.get("output_format"):
        extra["output_format"] = model["output_format"]
    if task_type != "auto":
        extra["omni_reference_task_type"] = task_type
    if _is_draft(label):
        extra["draft"] = True
    return model.get("duration"), auto_duration, ratio, extra


async def _generate(cls, client, model, label, content, prompt, ratio, seed, watermark, extras):
    """Submit, poll and collect through BytePlusVideoBase; returns core's outputs plus ours."""
    model_key = _model_key(label)
    duration, auto_duration, ratio, extra_api_params = build_seedance2_request_params(
        model, label, ratio, watermark
    )
    resolution = validate_seedance2_resolution(
        model_key, model.get("resolution"), draft_mode=_is_draft(label)
    )
    duration = validate_seedance2_duration(model_key, duration, auto_duration)
    helper = BytePlusVideoBase()
    result = await helper._common_generation_logic(
        client,
        prompt,
        duration,
        resolution,
        ratio,
        seed,
        extras["generation_count"],
        extras["filename_prefix"],
        extras["save_last_frame_batch"],
        extras["non_blocking"],
        cls.hidden.unique_id,
        model_name=resolve_model_id(model_key),
        content=content,
        forbidden_params=FORBIDDEN_PROMPT_PARAMS,
        enable_random_seed=False,
        is_auto_duration=auto_duration,
        extra_api_params=extra_api_params,
        return_last_frame=not _is_draft(label),
        node_class_type=cls.NODE_ID,
        workflow_prompt=getattr(cls.hidden, "prompt", None),
        as_list=True,
    )
    video, last_frame, response = result.args
    return comfy_io.NodeOutput(video, task_ids_from_response(response), last_frame, response)


def _extras(generation_count, filename_prefix, save_last_frame_batch, non_blocking):
    return {
        "generation_count": int(generation_count or 1),
        "filename_prefix": filename_prefix or DEFAULT_FILENAME_PREFIX,
        "save_last_frame_batch": bool(save_last_frame_batch),
        "non_blocking": bool(non_blocking),
    }


# --------------------------------------------------------------------------
# Image helpers (core's _prepare_seedance_image / FLF frame sizing)
# --------------------------------------------------------------------------

def _image_size(image):
    """(width, height) of an IMAGE tensor [B, H, W, C]."""
    return int(image.shape[-2]), int(image.shape[-3])


def _resize_image(image, width, height, crop="disabled"):
    import comfy.utils

    samples = image.movedim(-1, 1)
    return comfy.utils.common_upscale(samples, int(width), int(height), "lanczos", crop).movedim(1, -1)


def _check_image_ratio(image):
    width, height = _image_size(image)
    ratio = float(width) / float(height) if height else 0.0
    if ratio < IMAGE_MIN_RATIO or ratio > IMAGE_MAX_RATIO:
        raise BytePlusException(
            get_text(
                "popup_ref_image_ratio_out_of_range",
                min=IMAGE_MIN_RATIO,
                max=IMAGE_MAX_RATIO,
                ratio=f"{ratio:.4f}",
            )
        )


def prepare_seedance_image(image):
    """Aspect check, downscale to the 6000 px side limit, then size check (core's _prepare_seedance_image)."""
    _check_image_ratio(image)
    width, height = _image_size(image)
    longest = max(width, height)
    if longest > IMAGE_MAX_EDGE:
        scale = IMAGE_MAX_EDGE / float(longest)
        image = _resize_image(image, round(width * scale), round(height * scale))
    BytePlusVideoBase()._validate_reference_image_constraints(image)
    return image


def _check_frame_min_size(image):
    width, height = _image_size(image)
    if width < IMAGE_MIN_EDGE or height < IMAGE_MIN_EDGE:
        raise BytePlusException(
            get_text(
                "popup_ref_image_hw_out_of_range",
                min=IMAGE_MIN_EDGE,
                max=IMAGE_MAX_EDGE,
                width=width,
                height=height,
            )
        )


def seedance2_target_dims(resolution, ratio, image):
    """
    Exact output (width, height) for (resolution, ratio): the shorter side is the
    resolution number (1080p 16:9 -> 1920x1080). For "adaptive" the ratio is the
    supported one closest to the image's own aspect.
    """
    short = SEEDANCE2_RES_SHORT_SIDE[resolution]
    if ratio not in SEEDANCE2_RATIO_WH:
        width, height = _image_size(image)
        aspect = width / height
        ratio = min(
            SEEDANCE2_RATIO_WH,
            key=lambda k: abs(SEEDANCE2_RATIO_WH[k][0] / SEEDANCE2_RATIO_WH[k][1] - aspect),
        )
    rw, rh = SEEDANCE2_RATIO_WH[ratio]
    if rw >= rh:
        out_w, out_h = round(short * rw / rh), short
    else:
        out_w, out_h = short, round(short * rh / rw)
    return out_w - out_w % 2, out_h - out_h % 2


def resize_to_exact(image, width, height):
    """Center-crop to the target aspect and resize to exactly width x height (lanczos)."""
    return _resize_image(image, width, height, crop="center")


# --------------------------------------------------------------------------
# Reference video helpers (pixel limits, PyAV rescaling)
# --------------------------------------------------------------------------

def ref_video_pixel_limits(model_key, resolution):
    """
    Total-pixel range for reference videos: the documented BytePlus range, the
    same for every 2.x model and resolution. `max_target` is what auto_downscale
    aims for (core's per-resolution budget where it has one).
    """
    target = SEEDANCE2_REF_VIDEO_DOWNSCALE_TARGETS.get(model_key, {}).get(resolution)
    return {
        "min": REF_VIDEO_MIN_PIXELS,
        "max": REF_VIDEO_MAX_PIXELS,
        "max_target": min(target or REF_VIDEO_MAX_PIXELS, REF_VIDEO_MAX_PIXELS),
    }


def compute_downscale_dims(src_w, src_h, total_pixels):
    """Even (w, h) fitting total_pixels with the source aspect, or None if it already fits."""
    pixels = src_w * src_h
    if pixels <= total_pixels:
        return None
    scale = math.sqrt(total_pixels / pixels)
    long_src, short_src = max(src_w, src_h), min(src_w, src_h)
    long_new = max(2, int(long_src * scale) // 2 * 2)
    short_new = max(2, math.ceil(long_new * short_src / long_src / 2) * 2)
    if long_new * short_new > total_pixels:
        long_new = max(2, total_pixels // short_new // 2 * 2)
        short_new = max(2, math.ceil(long_new * short_src / long_src / 2) * 2)
    return (long_new, short_new) if src_w >= src_h else (short_new, long_new)


def compute_upscale_dims(src_w, src_h, total_pixels):
    """Even (w, h) reaching at least total_pixels with the source aspect, or None if already large enough."""
    pixels = src_w * src_h
    if pixels >= total_pixels:
        return None
    scale = math.sqrt(total_pixels / pixels)
    new_w = math.ceil(src_w * scale)
    new_h = math.ceil(src_h * scale)
    return new_w + new_w % 2, new_h + new_h % 2


def _video_stream_source(video):
    """A file path or BytesIO with the video's (trimmed) frames."""
    try:
        start, duration = video.get_active_trim_window()
    except Exception:
        start, duration = 0, 0
    if not (start or duration):
        try:
            return video.get_stream_source()
        except Exception:
            pass
    from comfy_api.latest import Types

    buffer = BytesIO()
    video.save_to(buffer, format=Types.VideoContainer.MP4, codec=Types.VideoCodec.H264)
    buffer.seek(0)
    return buffer


def scale_video(video, width, height):
    """Re-encode a VIDEO at exactly width x height (H.264 + AAC in MP4), keeping frame rate and audio."""
    import av
    from comfy_api.latest import InputImpl

    source = _video_stream_source(video)
    if hasattr(source, "seek"):
        source.seek(0)
    output = BytesIO()
    input_container = av.open(source, mode="r")
    try:
        output_container = av.open(output, mode="w", format="mp4")
        try:
            in_video = input_container.streams.video[0]
            rate = in_video.average_rate or video.get_frame_rate()
            video_stream = output_container.add_stream("h264", rate=rate)
            video_stream.width = int(width)
            video_stream.height = int(height)
            video_stream.pix_fmt = "yuv420p"
            audio_stream = None
            for stream in input_container.streams:
                if stream.type == "audio":
                    audio_stream = output_container.add_stream("aac", rate=stream.sample_rate)
                    audio_stream.sample_rate = stream.sample_rate
                    audio_stream.layout = stream.layout
                    break
            for frame in input_container.decode(video=0):
                frame = frame.reformat(width=int(width), height=int(height), format="yuv420p")
                # Fresh frame: the encoder assigns clean timestamps.
                frame = av.VideoFrame.from_ndarray(frame.to_ndarray(format="yuv420p"), format="yuv420p")
                for packet in video_stream.encode(frame):
                    output_container.mux(packet)
            for packet in video_stream.encode():
                output_container.mux(packet)
            if audio_stream is not None:
                input_container.seek(0)
                for audio_frame in input_container.decode(audio=0):
                    audio_frame.pts = None
                    for packet in audio_stream.encode(audio_frame):
                        output_container.mux(packet)
                for packet in audio_stream.encode():
                    output_container.mux(packet)
        finally:
            output_container.close()
    finally:
        input_container.close()
        if hasattr(source, "seek"):
            source.seek(0)
    output.seek(0)
    return InputImpl.VideoFromFile(output)


def _fit_reference_video(video, index, model_key, resolution, auto_downscale, auto_upscale):
    limits = ref_video_pixel_limits(model_key, resolution)
    try:
        width, height = video.get_dimensions()
    except Exception:
        return video
    dims = None
    if auto_downscale:
        dims = compute_downscale_dims(width, height, limits["max_target"])
    if dims is None and auto_upscale:
        dims = compute_upscale_dims(width, height, limits["min"])
    if dims is None:
        return video
    try:
        return scale_video(video, *dims)
    except Exception as e:
        raise BytePlusException(get_text("err_seedance2_ref_video_resize_failed", index=index, e=e))


def validate_reference_video(video, index, model_key, resolution, task_type="auto"):
    """
    BytePlus reference-video limits (sides, total pixels, aspect, fps, size,
    duration; edit tasks on 2.5 need 4 s). Returns the duration.
    """
    limits = ref_video_pixel_limits(model_key, resolution)
    try:
        width, height = video.get_dimensions()
    except Exception:
        width = height = None
    if width and height:
        if not (IMAGE_MIN_EDGE <= width <= IMAGE_MAX_EDGE and IMAGE_MIN_EDGE <= height <= IMAGE_MAX_EDGE):
            raise BytePlusException(
                get_text(
                    "popup_ref_video_hw_out_of_range",
                    min=IMAGE_MIN_EDGE,
                    max=IMAGE_MAX_EDGE,
                    width=width,
                    height=height,
                )
            )
        pixels = int(width) * int(height)
        details = {
            "index": index,
            "width": width,
            "height": height,
            "pixels": f"{pixels:,}",
            "min": f"{limits['min']:,}",
            "max": f"{limits['max']:,}",
        }
        if pixels < limits["min"]:
            raise BytePlusException(get_text("err_seedance2_ref_video_too_small", **details))
        if pixels > limits["max"]:
            raise BytePlusException(get_text("err_seedance2_ref_video_too_large", **details))
        ratio = float(width) / float(height)
        if ratio < IMAGE_MIN_RATIO or ratio > IMAGE_MAX_RATIO:
            raise BytePlusException(
                get_text(
                    "popup_ref_video_ratio_out_of_range",
                    min=IMAGE_MIN_RATIO,
                    max=IMAGE_MAX_RATIO,
                    ratio=f"{ratio:.4f}",
                )
            )
    try:
        fps = float(video.get_frame_rate())
    except Exception:
        fps = None
    if fps and not (REF_VIDEO_MIN_FPS <= fps <= REF_VIDEO_MAX_FPS):
        raise BytePlusException(
            get_text(
                "popup_ref_video_fps_out_of_range",
                min=REF_VIDEO_MIN_FPS,
                max=REF_VIDEO_MAX_FPS,
                fps=f"{fps:.3f}",
            )
        )
    try:
        duration = float(video.get_duration())
    except Exception:
        return 0.0
    if duration < REF_MEDIA_MIN_DURATION:
        raise BytePlusException(
            get_text(
                "err_seedance2_ref_media_too_short",
                kind="video",
                index=index,
                duration=f"{duration:.1f}",
                min=REF_MEDIA_MIN_DURATION,
            )
        )
    if task_type == "edit" and model_key in SEEDANCE_2_5_FAMILY and duration < SEEDANCE_2_5_EDIT_MIN_DURATION:
        raise BytePlusException(
            get_text(
                "err_seedance2_edit_video_too_short",
                index=index,
                duration=f"{duration:.1f}",
                min=SEEDANCE_2_5_EDIT_MIN_DURATION,
            )
        )
    size_bytes = video_source_size_bytes(video)
    if size_bytes is not None and size_bytes > REF_VIDEO_MAX_SIZE_MB * 1024 * 1024:
        raise BytePlusException(
            get_text(
                "popup_ref_video_size_exceeded",
                max_mb=REF_VIDEO_MAX_SIZE_MB,
                size_mb=f"{size_bytes / (1024.0 * 1024.0):.3f}",
            )
        )
    return duration


def build_asset_labels(asset_entries, n_images, n_videos, n_audios):
    """
    Slot number -> positional label ("Image 2"). Assets are appended after the
    connected references of the same type, so labels continue their count
    (core's _build_asset_labels). A repeated reference reuses its label.
    """
    counters = {"image": n_images, "video": n_videos, "audio": n_audios}
    names = {"image": "Image", "video": "Video", "audio": "Audio"}
    labels, by_uri = {}, {}
    for slot, kind, uri in asset_entries:
        if uri not in by_uri:
            counters[kind] += 1
            by_uri[uri] = f"{names[kind]} {counters[kind]}"
        labels[slot] = by_uri[uri]
    return labels


def rewrite_asset_refs(prompt, labels):
    """Case-insensitively replace 'assetN' / 'asset N' tokens (1-2 digits) with their labels."""
    if not labels:
        return prompt
    return _ASSET_REF_RE.sub(lambda m: labels.get(int(m.group(1)), m.group(0)), prompt)


async def _resolve_reference_assets(client, reference_assets):
    """[(slot, kind, uri)] for the filled asset slots, in slot order (resolved in parallel)."""
    slots = [
        (_slot_number(key), reference_assets[key])
        for key in sorted(reference_assets or {}, key=_slot_number)
        if str(reference_assets[key] or "").strip()
    ]
    resolved = await resolve_reference_values(client, [value for _slot, value in slots])
    return [(slot, item["kind"], item["uri"]) for (slot, _value), item in zip(slots, resolved)]


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------

class BytePlusSeedance2TextToVideo(comfy_io.ComfyNode):
    """Core's ByteDance2TextToVideoNode on BytePlus ModelArk."""

    NODE_ID = "BytePlusSeedance2TextToVideo"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id=cls.NODE_ID,
            display_name="BytePlus Seedance 2.5 Text to Video",
            category=GLOBAL_CATEGORY,
            description="Generate video using Seedance 2.5 or 2.0 models based on a text prompt. "
            "Calls BytePlus ModelArk directly with the API Client's key.",
            is_output_node=True,
            inputs=[
                BytePlusClientType.Input("client"),
                _model_input(lambda label: _text_option_inputs(label)),
                seed_input(),
                watermark_input(),
                *video_extra_inputs(),
            ],
            outputs=_generation_outputs(),
            hidden=[comfy_io.Hidden.unique_id, comfy_io.Hidden.prompt],
        )

    @classmethod
    async def execute(
        cls,
        client,
        model,
        seed=0,
        watermark=False,
        generation_count=1,
        filename_prefix=DEFAULT_FILENAME_PREFIX,
        save_last_frame_batch=False,
        non_blocking=False,
    ) -> comfy_io.NodeOutput:
        label = _selected_label(model)
        prompt = _require_prompt(model)
        validate_draft_output(cls, label)
        return await _generate(
            cls,
            client,
            model,
            label,
            content=[],
            prompt=prompt,
            ratio=model.get("ratio"),
            seed=seed,
            watermark=watermark,
            extras=_extras(generation_count, filename_prefix, save_last_frame_batch, non_blocking),
        )


class BytePlusSeedance2FirstLastFrame(comfy_io.ComfyNode):
    """Core's ByteDance2FirstLastFrameNode on BytePlus ModelArk."""

    NODE_ID = "BytePlusSeedance2FirstLastFrame"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        extras = video_extra_inputs()
        for extra in extras:
            # Optional like core's frame inputs, so the extras stay after them in the UI.
            extra.optional = True
        return comfy_io.Schema(
            node_id=cls.NODE_ID,
            display_name="BytePlus Seedance 2.5 First-Last-Frame to Video",
            category=GLOBAL_CATEGORY,
            description="Generate video using Seedance 2.5 or 2.0 from a first frame image "
            "and optional last frame image. Calls BytePlus ModelArk directly with the API Client's key.",
            is_output_node=True,
            inputs=[
                BytePlusClientType.Input("client"),
                _model_input(
                    lambda label: _text_option_inputs(label, with_ratio=False, default_ratio="adaptive")
                ),
                comfy_io.Image.Input(
                    "first_frame",
                    tooltip="First frame image for the video.",
                    optional=True,
                ),
                comfy_io.Image.Input(
                    "last_frame",
                    tooltip="Last frame image for the video.",
                    optional=True,
                ),
                comfy_io.String.Input(
                    "first_frame_asset_id",
                    default="",
                    tooltip="Seedance asset_id to use as the first frame. "
                    "Mutually exclusive with the first_frame image input. "
                    "Also accepts asset://<asset_id> or an https:// image link.",
                    optional=True,
                ),
                comfy_io.String.Input(
                    "last_frame_asset_id",
                    default="",
                    tooltip="Seedance asset_id to use as the last frame. "
                    "Mutually exclusive with the last_frame image input. "
                    "Also accepts asset://<asset_id> or an https:// image link.",
                    optional=True,
                ),
                seed_input(),
                watermark_input(),
                *extras,
            ],
            outputs=_generation_outputs(),
            hidden=[comfy_io.Hidden.unique_id, comfy_io.Hidden.prompt],
        )

    @classmethod
    async def execute(
        cls,
        client,
        model,
        seed=0,
        watermark=False,
        first_frame=None,
        last_frame=None,
        first_frame_asset_id="",
        last_frame_asset_id="",
        generation_count=1,
        filename_prefix=DEFAULT_FILENAME_PREFIX,
        save_last_frame_batch=False,
        non_blocking=False,
    ) -> comfy_io.NodeOutput:
        label = _selected_label(model)
        prompt = _require_prompt(model)
        validate_draft_output(cls, label)

        first_ref = str(first_frame_asset_id or "").strip()
        last_ref = str(last_frame_asset_id or "").strip()
        if first_frame is not None and first_ref:
            raise BytePlusException(get_text("err_seedance2_first_frame_both"))
        if first_frame is None and not first_ref:
            raise BytePlusException(get_text("err_seedance2_first_frame_missing"))
        if last_frame is not None and last_ref:
            raise BytePlusException(get_text("err_seedance2_last_frame_both"))

        if _is_2_5_family(label):
            # 2.5 takes ratio="adaptive" only here and keeps the first frame's own
            # aspect, so the frames are not pre-sized (as in core).
            request_ratio = "adaptive"
            first_frame = prepare_seedance_image(first_frame) if first_frame is not None else None
            last_frame = prepare_seedance_image(last_frame) if last_frame is not None else None
        elif first_ref or last_ref:
            request_ratio = model.get("ratio")
            first_frame = prepare_seedance_image(first_frame) if first_frame is not None else None
            last_frame = prepare_seedance_image(last_frame) if last_frame is not None else None
        else:
            # Core's 1080p FLF stretch fix: pre-size local frames to the exact
            # output pixel size and submit ratio="adaptive".
            request_ratio = "adaptive"
            target_dims = None
            for name in ("first_frame", "last_frame"):
                frame = first_frame if name == "first_frame" else last_frame
                if frame is None:
                    continue
                _check_image_ratio(frame)
                _check_frame_min_size(frame)
                if target_dims is None:
                    target_dims = seedance2_target_dims(model.get("resolution"), model.get("ratio"), frame)
                frame = resize_to_exact(frame, *target_dims)
                if name == "first_frame":
                    first_frame = frame
                else:
                    last_frame = frame

        first_uri = await resolve_typed_reference(client, first_ref, "image") if first_ref else None
        last_uri = await resolve_typed_reference(client, last_ref, "image") if last_ref else None

        helper = BytePlusVideoBase()
        content = []
        if first_uri:
            helper._append_media_url_content(content, first_uri, "image_url", "first_frame")
        else:
            helper._append_image_content(content, first_frame, "first_frame")
        if last_uri:
            helper._append_media_url_content(content, last_uri, "image_url", "last_frame")
        elif last_frame is not None:
            helper._append_image_content(content, last_frame, "last_frame")

        return await _generate(
            cls,
            client,
            model,
            label,
            content=content,
            prompt=prompt,
            ratio=request_ratio,
            seed=seed,
            watermark=watermark,
            extras=_extras(generation_count, filename_prefix, save_last_frame_batch, non_blocking),
        )


class BytePlusSeedance2Reference(comfy_io.ComfyNode):
    """Core's ByteDance2ReferenceNodeV2 on BytePlus ModelArk."""

    NODE_ID = "BytePlusSeedance2Reference"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id=cls.NODE_ID,
            display_name="BytePlus Seedance 2.5 Reference to Video",
            category=GLOBAL_CATEGORY,
            description="Generate, edit, or extend video using Seedance 2.5 or 2.0 with reference "
            "images, videos, and audio. Supports multimodal reference, video editing, and video extension. "
            "Calls BytePlus ModelArk directly with the API Client's key; connected reference videos are "
            "uploaded to Comfy.org storage (needs a Comfy.org login), or pass links / asset IDs in "
            "reference_assets.",
            is_output_node=True,
            inputs=[
                BytePlusClientType.Input("client"),
                _model_input(_reference_option_inputs),
                seed_input(),
                watermark_input(),
                *video_extra_inputs(),
            ],
            outputs=_generation_outputs(),
            hidden=[
                comfy_io.Hidden.auth_token_comfy_org,
                comfy_io.Hidden.api_key_comfy_org,
                comfy_io.Hidden.unique_id,
                comfy_io.Hidden.prompt,
            ],
        )

    @classmethod
    async def execute(
        cls,
        client,
        model,
        seed=0,
        watermark=False,
        generation_count=1,
        filename_prefix=DEFAULT_FILENAME_PREFIX,
        save_last_frame_batch=False,
        non_blocking=False,
    ) -> comfy_io.NodeOutput:
        label = _selected_label(model)
        prompt = _require_prompt(model)
        validate_draft_output(cls, label)
        model_key = _model_key(label)
        is_2_5 = _is_2_5_family(label)
        resolution = model.get("resolution")
        limits = VIDEO_2_MODEL_REFERENCE_LIMITS[model_key]
        max_seconds = REF_MEDIA_MAX_DURATION_SEEDANCE_2_5 if is_2_5 else REF_MEDIA_MAX_DURATION

        images = _ordered_values(model.get("reference_images"))
        videos = _ordered_values(model.get("reference_videos"))
        audios = _ordered_values(model.get("reference_audios"))
        asset_entries = await _resolve_reference_assets(client, model.get("reference_assets"))
        unique_assets = {}
        for _slot, kind, uri in asset_entries:
            unique_assets.setdefault(uri, kind)
        asset_counts = {kind: 0 for kind in ("image", "video", "audio")}
        for kind in unique_assets.values():
            asset_counts[kind] += 1

        if not images and not videos and not asset_counts["image"] and not asset_counts["video"]:
            if not is_2_5 or not (audios or asset_counts["audio"]):
                raise BytePlusException(get_text("err_seedance2_reference_required"))
        for kind, local, key in (("image", images, "images"), ("video", videos, "videos")):
            total = len(local) + asset_counts[kind]
            if total > limits[key]:
                raise BytePlusException(
                    get_text(
                        "err_seedance2_too_many_references",
                        kind=kind,
                        total=total,
                        local=len(local),
                        assets=asset_counts[kind],
                        max=limits[key],
                    )
                )
        task_type = model.get("task_type")
        if task_type in ("edit", "extend") and not (videos or asset_counts["video"]):
            raise BytePlusException(
                get_text(
                    "err_seedance2_task_type_needs_video",
                    task_type=task_type,
                    verb="change" if task_type == "edit" else "continue",
                )
            )
        total_audios = len(audios) + asset_counts["audio"]
        if total_audios > limits["audios"]:
            raise BytePlusException(
                get_text(
                    "err_seedance2_too_many_references",
                    kind="audio",
                    total=total_audios,
                    local=len(audios),
                    assets=asset_counts["audio"],
                    max=limits["audios"],
                )
            )

        images = [prepare_seedance_image(image) for image in images]
        auto_downscale = bool(model.get("auto_downscale", True))
        auto_upscale = bool(model.get("auto_upscale", False))
        videos = [
            _fit_reference_video(video, index, model_key, resolution, auto_downscale, auto_upscale)
            for index, video in enumerate(videos, 1)
        ]
        total_video_duration = sum(
            validate_reference_video(video, index, model_key, resolution, task_type=task_type)
            for index, video in enumerate(videos, 1)
        )
        if total_video_duration > max_seconds:
            raise BytePlusException(
                get_text(
                    "err_seedance2_ref_media_total_too_long",
                    kind="video",
                    duration=f"{total_video_duration:.1f}",
                    max=max_seconds,
                )
            )

        helper = BytePlusVideoBase()
        image_items, image_bytes = [], 0
        for image in images:
            image_bytes += helper._append_image_content(image_items, image, "reference_image")
        if image_bytes / (1024.0 * 1024.0) > REF_IMAGE_MAX_TOTAL_REQUEST_MB:
            raise BytePlusException(
                get_text(
                    "popup_ref_image_total_size_exceeded",
                    max_mb=REF_IMAGE_MAX_TOTAL_REQUEST_MB,
                    size_mb=f"{image_bytes / (1024.0 * 1024.0):.3f}",
                )
            )
        audio_items, audio_bytes, total_audio_duration = [], 0, 0.0
        for audio in audios:
            duration, request_bytes = helper._append_audio_content(
                audio_items, audio, "reference_audio", max_duration=max_seconds
            )
            total_audio_duration += duration
            audio_bytes += request_bytes
        if total_audio_duration > max_seconds:
            raise BytePlusException(
                get_text(
                    "err_seedance2_ref_media_total_too_long",
                    kind="audio",
                    duration=f"{total_audio_duration:.1f}",
                    max=max_seconds,
                )
            )
        if audio_bytes / (1024.0 * 1024.0) > REF_AUDIO_MAX_TOTAL_REQUEST_MB:
            raise BytePlusException(
                get_text(
                    "popup_ref_audio_total_size_exceeded",
                    max_mb=REF_AUDIO_MAX_TOTAL_REQUEST_MB,
                    size_mb=f"{audio_bytes / (1024.0 * 1024.0):.3f}",
                )
            )

        labels = build_asset_labels(asset_entries, len(images), len(videos), len(audios))
        prompt = rewrite_asset_refs(prompt, labels)

        video_items = []
        if videos:
            video_urls = await upload_videos_to_comfy_storage_cached(
                cls,
                videos,
                helper,
                unavailable_key="err_comfy_upload_unavailable_reference",
                failed_key="err_comfy_upload_failed_reference",
            )
            for url in video_urls:
                helper._append_media_url_content(video_items, url, "video_url", "reference_video")

        asset_items = {"image": [], "video": [], "audio": []}
        for uri, kind in unique_assets.items():
            media_type = f"{kind}_url"
            helper._append_media_url_content(asset_items[kind], uri, media_type, f"reference_{kind}")

        content = (
            image_items
            + video_items
            + audio_items
            + asset_items["image"]
            + asset_items["video"]
            + asset_items["audio"]
        )
        return await _generate(
            cls,
            client,
            model,
            label,
            content=content,
            prompt=prompt,
            ratio=model.get("ratio"),
            seed=seed,
            watermark=watermark,
            extras=_extras(generation_count, filename_prefix, save_last_frame_batch, non_blocking),
        )


# --------------------------------------------------------------------------
# Draft to Final
# --------------------------------------------------------------------------

_FAILED_TASK_STATUSES = ("failed", "cancelled", "expired")
# A draft can be rendered for 7 days after it was created.
DRAFT_TASK_VALID_SECONDS = 7 * 24 * 3600


def video_model_key_for_id(model_id):
    """VIDEO_MODEL_MAP key for a (dated) model ID from a task, or None."""
    text = str(model_id or "").strip()
    for key, value in VIDEO_MODEL_MAP.items():
        if text in (key, value):
            return key
    matches = [key for key in VIDEO_MODEL_MAP if text.startswith(key + "-")]
    return max(matches, key=len) if matches else None


def draft_final_plan(model_key):
    """(final resolution, service_tier, execution_expires_after) for drafts of this model."""
    if model_key in SEEDANCE_DRAFT_FINAL_RESOLUTIONS:
        return SEEDANCE_DRAFT_FINAL_RESOLUTIONS[model_key][0], None, None
    return None


async def _get_draft_task(client, task_id):
    try:
        return await asyncio.to_thread(client.ark.content_generation.tasks.get, task_id=task_id)
    except comfy.model_management.InterruptProcessingException:
        raise
    except Exception as e:
        raise BytePlusException(
            get_text("err_draft_lookup_failed", task_id=task_id, e=format_api_error(e).replace(LOG_PREFIX, "", 1))
        )


async def describe_drafts(client, draft_ids):
    """Look up the draft tasks; returns (model_key, model_id, first task)."""
    tasks = await asyncio.gather(*[_get_draft_task(client, task_id) for task_id in draft_ids])
    model_keys = []
    for task_id, task in zip(draft_ids, tasks):
        # The query API documents `draft` only for Seedance 1.5 Pro (2.5 returns it
        # too), so also reject finals and non-480p tasks, and drafts past 7 days.
        if getattr(task, "draft", None) is False:
            raise BytePlusException(get_text("err_draft_not_a_draft", task_id=task_id))
        source_draft = getattr(task, "draft_task_id", None)
        if source_draft:
            raise BytePlusException(
                get_text("err_draft_is_final", task_id=task_id, draft_task_id=source_draft)
            )
        task_resolution = str(getattr(task, "resolution", "") or "")
        if task_resolution and task_resolution != SEEDANCE_DRAFT_RESOLUTION:
            raise BytePlusException(get_text("err_draft_not_a_draft", task_id=task_id))
        created_at = getattr(task, "created_at", None)
        if isinstance(created_at, (int, float)) and created_at > 0:
            if time.time() - float(created_at) > DRAFT_TASK_VALID_SECONDS:
                raise BytePlusException(get_text("err_draft_expired", task_id=task_id))
        status = str(getattr(task, "status", "") or "")
        if status in _FAILED_TASK_STATUSES:
            raise BytePlusException(get_text("err_draft_failed", task_id=task_id, status=status))
        if status and status != "succeeded":
            raise BytePlusException(get_text("err_draft_not_ready", task_id=task_id, status=status))
        model_id = str(getattr(task, "model", "") or "")
        model_key = video_model_key_for_id(model_id)
        if model_key is None or draft_final_plan(model_key) is None:
            raise BytePlusException(
                get_text("err_draft_model_unsupported", task_id=task_id, model=model_id or "unknown")
            )
        model_keys.append(model_key)
    if len(set(model_keys)) > 1:
        raise BytePlusException(get_text("err_draft_mixed_models", models=", ".join(sorted(set(model_keys)))))
    first = tasks[0]
    model_key = model_keys[0]
    # The final uses the draft's own dated model ID (the UI name maps to the current one).
    model_id = str(getattr(first, "model", "") or "")
    if model_id in ("", model_key):
        model_id = VIDEO_MODEL_MAP[model_key]
    return model_key, model_id, first


class BytePlusSeedanceDraftToFinal(comfy_io.ComfyNode):
    """Core's ByteDance2DraftToFinalVideoNode on BytePlus ModelArk (also Seedance 2.5 Premium drafts)."""

    NODE_ID = "BytePlusSeedanceDraftToFinal"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id=cls.NODE_ID,
            display_name="BytePlus Seedance 2.5 Draft to Final Video",
            category=GLOBAL_CATEGORY,
            description="Render the final video of a Seedance 2.5 Draft (1080p) or Seedance 2.5 Premium "
            "Draft (4k). The final keeps the draft's scene and motion, and reuses its prompt, references, "
            "duration, aspect ratio, and audio setting. The model is read from the draft task.",
            is_output_node=True,
            inputs=[
                BytePlusClientType.Input("client"),
                comfy_io.String.Input(
                    "draft_task_id",
                    default="",
                    tooltip="The draft_task_id output of a BytePlus Seedance 2.5 node run with a Draft model "
                    "(Seedance 2.5 Draft or Seedance 2.5 Premium Draft), or pasted "
                    "draft task IDs (one per line, or separated by commas). Set that node's seed control to "
                    "fixed, otherwise the next run generates a new draft instead of reusing the one you "
                    "reviewed. A draft can be rendered for 7 days after it was created.",
                ),
                watermark_input(),
                *video_extra_inputs(),
            ],
            outputs=[
                video_list_output(),
                last_frame_batch_output(),
                comfy_io.String.Output("response", tooltip="Task responses as JSON."),
            ],
            hidden=[comfy_io.Hidden.unique_id, comfy_io.Hidden.prompt],
        )

    @classmethod
    async def execute(
        cls,
        client,
        draft_task_id="",
        watermark=False,
        generation_count=1,
        filename_prefix=DEFAULT_FILENAME_PREFIX,
        save_last_frame_batch=False,
        non_blocking=False,
    ) -> comfy_io.NodeOutput:
        draft_ids = _parse_draft_task_ids(draft_task_id)
        if not draft_ids:
            raise BytePlusException(get_text("err_draft_task_id_empty"))
        model_key, model_id, first_task = await describe_drafts(client, draft_ids)
        resolution, service_tier, execution_expires_after = draft_final_plan(model_key)
        content, count = build_draft_final_content(draft_ids, int(generation_count or 1))
        extra_api_params = {"resolution": resolution, "watermark": bool(watermark)}
        output_format = str(getattr(first_task, "output_format", "") or "")
        if output_format and output_format != "mp4":
            extra_api_params["output_format"] = output_format
        try:
            duration = float(getattr(first_task, "duration", 0) or 0)
        except (TypeError, ValueError):
            duration = 0.0
        log_msg("draft_final_render", resolution=resolution, count=len(draft_ids), model=model_id)
        node_count = get_node_count_in_workflow(cls.NODE_ID, prompt=getattr(cls.hidden, "prompt", None))
        result = await BytePlusVideoBase()._run_prebuilt_content(
            client,
            cls.hidden.unique_id,
            model_id,
            content,
            estimation_duration=duration if duration > 0 else 5,
            resolution=resolution,
            generation_count=count,
            filename_prefix=filename_prefix or DEFAULT_FILENAME_PREFIX,
            save_last_frame_batch=bool(save_last_frame_batch),
            non_blocking=bool(non_blocking),
            extra_api_params=extra_api_params,
            service_tier=service_tier,
            execution_expires_after=execution_expires_after,
            ignore_errors=node_count > 1,
            as_list=True,
            workflow_prompt=getattr(cls.hidden, "prompt", None),
        )
        return comfy_io.NodeOutput(*result.args)


# Registered in __init__.py.
NODES = [
    BytePlusSeedance2TextToVideo,
    BytePlusSeedance2FirstLastFrame,
    BytePlusSeedance2Reference,
    BytePlusSeedanceDraftToFinal,
]
