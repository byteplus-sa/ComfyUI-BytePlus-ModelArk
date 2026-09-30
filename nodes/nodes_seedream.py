"""
Seedream nodes shaped like ComfyUI core's ByteDanceSeedreamNodeV3 and
ByteDanceSeedreamLayerSeparationNodeV2 (comfy_api_nodes/nodes_bytedance.py):
the same inputs, defaults, tooltips and outputs, but calling BytePlus ModelArk
directly with the API Client's key. This pack's extras (parallel generations,
output format, transparent background, saving layer files) come after core's
inputs as advanced widgets, and extra outputs come after core's.
"""
import asyncio
import json
import logging
import math

import numpy
import torch
import comfy.model_management
import comfy.utils
from comfy_api.latest import io as comfy_io
from byteplussdkarkruntime.types.images.images import (
    OptimizePromptOptions,
    SequentialImageGenerationOptions,
)

from .constants import MIN_ASPECT_RATIO, MAX_ASPECT_RATIO
from .core_style import SEED_MAX, seed_input, watermark_input, generation_count_input
from .executor import BytePlusGenerationExecutor
from .models_config import (
    SEEDREAM_PRO,
    SEEDREAM_FLASH,
    SEEDREAM_MODELS,
    SEEDREAM_MODEL_CAPS,
    SEEDREAM_MAX_TOTAL_IMAGES,
    SEEDREAM_LAYER_SEPARATION_MODELS,
    SEEDREAM_LAYER_SIZES,
)
from .nodes_image import (
    _b64_to_rgba_image,
    _bbox_dict,
    _collect_image_tensors,
    _image_to_png_data_uri,
    _prepare_multi_image_inputs,
    _save_layer_pngs,
    _split_rgba,
    request_url_images,
)
from .nodes_image_schema import (
    SEEDREAM_PRESETS,
    seedream_adaptive_label,
    seedream_size_options,
)
from .nodes_shared import (
    GLOBAL_CATEGORY,
    BytePlusClientType,
    BytePlusException,
    create_white_image_tensor,
    format_api_error,
    get_node_count_in_workflow,
    get_text,
    log_msg,
    safe_cat_tensors,
)

logger = logging.getLogger("BytePlus")

# Core's tooltips (kept verbatim so the nodes read like core's).
SEEDREAM_DESCRIPTION = (
    "Unified text-to-image generation and precise single-sentence editing at up to 4K resolution."
)
SEEDREAM_SEED_TOOLTIP = "Seed to use for generation."
IMAGE_WATERMARK_TOOLTIP = 'Whether to add an "AI generated" watermark to the image.'
LAYERS_WATERMARK_TOOLTIP = 'Whether to add an "AI generated" watermark to the images.'
LAYER_SEPARATION_DESCRIPTION = (
    "Decompose an image into a background plate plus up to 16 repositionable transparent layers, "
    "each with stacking order, bounding box, name and description."
)

URL_MODELS = (SEEDREAM_PRO, SEEDREAM_FLASH)
LAYER_MIN_SIDE = 512
# Core downscales the layer-separation input to about 4 MP before upload.
LAYER_INPUT_MAX_PIXELS = 2048 * 2048
# z_index_of() value for a missing or unreadable z_index (core's sentinel).
_NO_Z_INDEX = 1_000_000


def _json_default(value):
    """json.dumps fallback for SDK models (e.g. streamed usage) in the response output."""
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(exclude_none=True)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return str(value)


# --- BytePlus Seedream 4.5 & 5.0 ---------------------------------------------


def _seedream_model_inputs(model):
    """Core's _seedream_model_inputs for one model option, then this pack's extras."""
    caps = SEEDREAM_MODEL_CAPS[model]
    max_refs = caps["max_refs"]
    inputs = [
        comfy_io.Combo.Input(
            "size_preset",
            options=seedream_size_options(model),
            tooltip="Pick a recommended size. Select Custom to use the width and height below.",
        ),
        comfy_io.Int.Input(
            "width",
            default=2048,
            min=1024,
            max=caps["max_width"],
            step=2,
            tooltip="Custom width for image. Value is working only if `size_preset` is set to `Custom`",
        ),
        comfy_io.Int.Input(
            "height",
            default=2048,
            min=1024,
            max=caps["max_height"],
            step=2,
            tooltip="Custom height for image. Value is working only if `size_preset` is set to `Custom`",
        ),
    ]
    if caps["batch"]:
        inputs.append(
            comfy_io.Int.Input(
                "max_images",
                default=1,
                min=1,
                max=max_refs,
                step=1,
                display_mode=comfy_io.NumberDisplay.number,
                tooltip="Maximum number of images to generate. With 1, exactly one image is produced. "
                "With >1, the model generates between 1 and max_images related images "
                "(e.g., story scenes, character variations). "
                "Total images (input + generated) cannot exceed 15.",
            )
        )
    inputs.append(
        comfy_io.Autogrow.Input(
            "images",
            template=comfy_io.Autogrow.TemplateNames(
                comfy_io.Image.Input("image"),
                names=[f"image_{i}" for i in range(1, max_refs + 1)],
                min=0,
            ),
            tooltip=f"Optional reference image(s) for image-to-image or multi-reference generation. "
            f"Up to {max_refs} images.",
        )
    )
    if caps["batch"]:
        inputs.append(
            comfy_io.Boolean.Input(
                "fail_on_partial",
                default=False,
                tooltip="If enabled, abort execution if any requested images are missing or return an error.",
                advanced=True,
            )
        )
    if caps["fast"]:
        inputs.append(
            comfy_io.Combo.Input(
                "prompt_optimization",
                options=["standard", "fast"],
                default="standard",
                tooltip="Prompt-optimization mode when reference images are provided: "
                "'standard' gives higher quality, 'fast' shorter generation time.",
                advanced=True,
            )
        )
    inputs.append(seed_input(default=42, tooltip=SEEDREAM_SEED_TOOLTIP))
    inputs.append(watermark_input(tooltip=IMAGE_WATERMARK_TOOLTIP))
    if caps["thinking"]:
        inputs.append(
            comfy_io.Boolean.Input(
                "thinking",
                default=True,
                tooltip=(
                    "Enable the model's prompt-optimization reasoning ('thinking') for better adherence. "
                    "Can substantially increase generation time — notably on Seedream 5.0 Pro. "
                    "Can only be disabled for text-to-image (not when reference images are provided)."
                ),
                advanced=True,
            )
        )

    # This pack's extras.
    inputs.append(generation_count_input())
    if model in URL_MODELS:
        inputs.extend(
            [
                comfy_io.Combo.Input(
                    "output_format",
                    options=["jpeg", "png"],
                    default="jpeg",
                    tooltip="File format of the generated image. A transparent background needs png.",
                    advanced=True,
                ),
                comfy_io.Combo.Input(
                    "background",
                    options=["opaque", "transparent"],
                    default="opaque",
                    tooltip=(
                        "transparent: edit one reference image that has an alpha channel "
                        "(connect its mask to reference_mask) and return a PNG with "
                        "transparency, also as the mask output. Needs output_format png."
                    ),
                    advanced=True,
                ),
                comfy_io.Mask.Input(
                    "reference_mask",
                    optional=True,
                    tooltip=(
                        "Alpha of the reference image for a transparent background "
                        "(connect the MASK output of Load Image)."
                    ),
                ),
            ]
        )
    return inputs


def resolve_seedream_size(model, size_preset, width, height):
    """
    size_preset -> the API's size value: "WxH" for core's presets and Custom
    (checked against the model's pixel range, as core does), or a resolution
    level such as "2K" for this pack's "(adaptive)" presets.
    """
    caps = SEEDREAM_MODEL_CAPS[model]
    for level in caps["adaptive_sizes"]:
        if size_preset == seedream_adaptive_label(level):
            return level
    w = h = None
    for label, preset_w, preset_h in SEEDREAM_PRESETS[model]:
        if label == size_preset:
            w, h = preset_w, preset_h
            break
    if w is None or h is None:
        w, h = int(width), int(height)

    pixels = w * h
    if pixels < caps["min_pixels"]:
        raise BytePlusException(
            get_text(
                "seedream_err_min_pixels",
                min_mp=caps["min_pixels"] / 1_000_000.0,
                mp=pixels / 1_000_000.0,
            )
        )
    if pixels > caps["max_pixels"]:
        raise BytePlusException(
            get_text(
                "seedream_err_max_pixels",
                max_mp=caps["max_pixels"] / 1_000_000.0,
                mp=pixels / 1_000_000.0,
            )
        )
    return f"{w}x{h}"


def _check_aspect_ratio(tensor, error_key, **kwargs):
    height, width = int(tensor.shape[-3]), int(tensor.shape[-2])
    ratio = width / height if height else 0.0
    if not (MIN_ASPECT_RATIO <= ratio <= MAX_ASPECT_RATIO):
        raise BytePlusException(get_text(error_key, ratio=ratio, **kwargs))


def build_seedream_plan(model_config, prompt):
    """
    Validate the inputs of one run like core's ByteDanceSeedreamNodeV3 and
    return what the requests need. model_config is the DynamicCombo value.
    """
    model = model_config.get("model")
    if model not in SEEDREAM_MODELS:
        raise BytePlusException(get_text("seedream_err_unknown_model", model=model))
    if not str(prompt or "").strip():
        raise BytePlusException(get_text("seedream_err_prompt_empty"))
    caps = SEEDREAM_MODEL_CAPS[model]
    is_url_model = model in URL_MODELS

    size = resolve_seedream_size(
        model,
        model_config.get("size_preset", SEEDREAM_PRESETS[model][0][0]),
        model_config.get("width", 2048),
        model_config.get("height", 2048),
    )
    max_images = int(model_config.get("max_images", 1)) if caps["batch"] else 1
    thinking = bool(model_config.get("thinking", True))
    prompt_optimization = model_config.get("prompt_optimization", "standard")

    images = model_config.get("images") or {}
    ref_tensors = _collect_image_tensors(images)
    n_refs = len(ref_tensors)
    if n_refs > caps["max_refs"]:
        raise BytePlusException(
            get_text("seedream_err_ref_count", max=caps["max_refs"], count=n_refs)
        )
    if max_images > 1 and n_refs + max_images > SEEDREAM_MAX_TOTAL_IMAGES:
        raise BytePlusException(
            get_text(
                "seedream_err_refs_plus_outputs",
                max_images=max_images,
                count=n_refs,
                limit=SEEDREAM_MAX_TOTAL_IMAGES,
            )
        )
    if not thinking and n_refs > 0:
        raise BytePlusException(get_text("seedream_err_thinking_with_refs"))
    for index, tensor in enumerate(ref_tensors, start=1):
        _check_aspect_ratio(tensor, "seedream_err_ref_aspect", index=index)

    output_format = model_config.get("output_format", "jpeg")
    transparent = is_url_model and model_config.get("background", "opaque") == "transparent"
    if transparent:
        if n_refs != 1:
            raise BytePlusException(get_text("err_transparent_needs_one_image", n=n_refs))
        if output_format != "png":
            raise BytePlusException(get_text("err_transparent_needs_png"))
        # The API needs an input with an alpha channel; without a mask the
        # image is sent fully opaque.
        image_param = _image_to_png_data_uri(
            ref_tensors[0], model_config.get("reference_mask"), with_alpha=True
        )
    else:
        _, image_param = _prepare_multi_image_inputs(images)

    # Core: thinking on/off for text-to-image (every model but Flash); "fast"
    # mode when reference images are given (Pro only).
    optimize_prompt_options = None
    if n_refs == 0 and model != SEEDREAM_FLASH:
        optimize_prompt_options = OptimizePromptOptions(
            thinking="enabled" if thinking else "disabled"
        )
    elif prompt_optimization == "fast":
        optimize_prompt_options = OptimizePromptOptions(mode="fast")

    return {
        "model": model,
        "model_id": SEEDREAM_MODELS[model],
        "prompt": prompt,
        "size": size,
        "is_url_model": is_url_model,
        "max_images": max_images,
        "sequential": None if is_url_model else ("auto" if max_images > 1 else "disabled"),
        "image_param": image_param,
        "n_refs": n_refs,
        "optimize_prompt_options": optimize_prompt_options,
        "seed": int(model_config.get("seed", 42)),
        "watermark": bool(model_config.get("watermark", False)),
        "fail_on_partial": bool(model_config.get("fail_on_partial", False)) and caps["batch"],
        "generation_count": max(1, int(model_config.get("generation_count", 1))),
        "output_format": output_format,
        "transparent": transparent,
    }


def build_seedream_request(plan, idx=0):
    """images.generate kwargs for generation `idx` of a plan."""
    request = {
        "model": plan["model_id"],
        "prompt": plan["prompt"],
        "size": plan["size"],
        "response_format": "url" if plan["is_url_model"] else "b64_json",
        "watermark": plan["watermark"],
        "seed": (plan["seed"] + idx) % (SEED_MAX + 1),
    }
    if plan["is_url_model"]:
        request["output_format"] = plan["output_format"]
        if plan["transparent"]:
            request["extra_body"] = {"background": "transparent"}
    else:
        request["sequential_image_generation"] = plan["sequential"]
        if plan["sequential"] == "auto":
            request["sequential_image_generation_options"] = SequentialImageGenerationOptions(
                max_images=plan["max_images"]
            )
    if plan["optimize_prompt_options"] is not None:
        request["optimize_prompt_options"] = plan["optimize_prompt_options"]
    if plan["image_param"]:
        request["image"] = plan["image_param"]
    return request


class BytePlusSeedream(comfy_io.ComfyNode):
    """
    Seedream 5.0 Pro / Flash / Lite, 4.5 and 4.0 with core's ByteDanceSeedreamNodeV3
    inputs. Pro and Flash return one image by URL; Lite, 4.5 and 4.0 stream b64
    images and can generate up to max_images related images.
    """

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusSeedream",
            display_name="BytePlus Seedream 4.5 & 5.0",
            category=GLOBAL_CATEGORY,
            description=SEEDREAM_DESCRIPTION,
            inputs=[
                BytePlusClientType.Input("client"),
                comfy_io.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip="Text prompt for creating or editing an image.",
                ),
                comfy_io.DynamicCombo.Input(
                    "model",
                    options=[
                        comfy_io.DynamicCombo.Option(model, _seedream_model_inputs(model))
                        for model in SEEDREAM_MODELS
                    ],
                ),
            ],
            outputs=[
                comfy_io.Image.Output(),
                comfy_io.String.Output(
                    display_name="response",
                    tooltip="Request metadata of each generation as JSON.",
                ),
                comfy_io.Mask.Output(
                    display_name="mask",
                    tooltip=(
                        "Transparency of the output (1 = transparent, like Load Image). "
                        "Empty unless background is transparent."
                    ),
                ),
            ],
            hidden=[comfy_io.Hidden.unique_id, comfy_io.Hidden.prompt],
        )

    @classmethod
    async def execute(cls, client, prompt, model) -> comfy_io.NodeOutput:
        plan = build_seedream_plan(model if isinstance(model, dict) else {"model": model}, prompt)
        model_id = plan["model_id"]
        generation_count = plan["generation_count"]
        batch_mode = plan["max_images"] > 1

        client.check_quota(model_id, generation_count * plan["max_images"])
        if generation_count > 1:
            log_msg("batch_submit_start", count=generation_count, model=model_id)

        node_count = get_node_count_in_workflow("BytePlusSeedream", prompt=cls.hidden.prompt)
        executor = BytePlusGenerationExecutor(
            client, cls.hidden.unique_id, ignore_errors=node_count > 1
        )
        partial_failures = []

        async def _generate_single(idx, session):
            request = build_seedream_request(plan, idx)
            if plan["is_url_model"]:
                return await request_url_images(
                    session, client.ark, request, idx, model_id, plan["transparent"]
                )
            return await executor.stream_generation_helper(
                session,
                client.ark,
                request,
                idx,
                batch_mode,
                generation_count,
                partial_failures=partial_failures,
            )

        tensors, metadata = await executor.run_parallel_requests(
            generation_count, _generate_single
        )
        if not tensors:
            empty = create_white_image_tensor()
            return comfy_io.NodeOutput(empty, "[]", torch.zeros(empty.shape[:3]))

        received = sum(int(t.shape[0]) for t in tensors)
        try:
            client.update_usage(model_id, received)
        except Exception:
            pass
        if plan["fail_on_partial"]:
            if partial_failures:
                raise BytePlusException(
                    get_text(
                        "seedream_err_partial",
                        received=received,
                        requested=received + len(partial_failures),
                    )
                )
            if len(tensors) < generation_count:
                raise BytePlusException(
                    get_text(
                        "seedream_err_partial_generations",
                        received=len(tensors),
                        requested=generation_count,
                    )
                )
        for entry in metadata:
            failed = [
                {key: value for key, value in failure.items() if key != "batch_index"}
                for failure in partial_failures
                if failure["batch_index"] == entry.get("batch_index")
            ]
            if failed:
                entry["failed_images"] = failed

        output_image, output_mask = _split_rgba(safe_cat_tensors(tensors))
        return comfy_io.NodeOutput(
            output_image,
            json.dumps(metadata, indent=2, ensure_ascii=False, default=_json_default),
            output_mask,
        )


# --- BytePlus Seedream 5.0 Layer Separation ----------------------------------


def _layer_separation_inputs(supports_fast):
    """Core's _seedream_layer_separation_inputs, then this pack's extras."""
    inputs = [
        comfy_io.Image.Input(
            "image",
            tooltip=(
                "The image to separate. Exactly one image, at least 512x512 pixels, aspect ratio "
                "between 1:16 and 16:1. Inputs larger than about 4MP are downscaled before upload."
            ),
        ),
        comfy_io.String.Input(
            "prompt",
            multiline=True,
            default="",
            tooltip=(
                "How to separate the image. Leave empty to auto-detect and separate all major elements. "
                "Describe elements in natural language to control the separation, or target exact regions "
                "with <bbox>left top right bottom</bbox> tags (0-1000 per-mille coordinates)."
            ),
        ),
        comfy_io.Combo.Input(
            "size",
            options=SEEDREAM_LAYER_SIZES,
            default="auto",
            tooltip="Output resolution level. 'auto' follows the input image size (clamped to the 1K-2K range).",
        ),
        seed_input(default=42, tooltip=SEEDREAM_SEED_TOOLTIP),
    ]
    if supports_fast:
        inputs.append(
            comfy_io.Combo.Input(
                "prompt_optimization",
                options=["standard", "fast"],
                default="standard",
                advanced=True,
                tooltip="Prompt-optimization mode: 'standard' gives higher quality, 'fast' shorter generation time.",
            )
        )
    inputs.extend(
        [
            watermark_input(tooltip=LAYERS_WATERMARK_TOOLTIP),
            comfy_io.Boolean.Input(
                "crop_layers",
                default=False,
                label_on="minimal size",
                label_off="full canvas",
                tooltip=(
                    "Geometry of the layers/masks batch outputs (layer_stack is unaffected and always "
                    "tight). Full canvas: each layer on a base-sized canvas at its bounding-box position - "
                    "recompose directly with ImageCompositeMasked. Minimal size: each layer cropped to its "
                    "bounding box (padded to the largest layer for batching) - much smaller tensors; "
                    "rebuild placement with Layers From Bounding Boxes using the bboxes output."
                ),
            ),
            # This pack's extras.
            comfy_io.Combo.Input(
                "output_format",
                options=["png", "jpeg"],
                default="png",
                tooltip="Format of the base image. Layers are always PNG.",
                advanced=True,
            ),
            comfy_io.Boolean.Input(
                "save_layers",
                default=False,
                tooltip="Also save the base image and the original layer PNGs to the output folder.",
                advanced=True,
            ),
            comfy_io.String.Input(
                "filename_prefix",
                default="BytePlus/Layers/Seedream",
                tooltip="Output folder prefix for the files written when save_layers is on.",
                advanced=True,
            ),
        ]
    )
    return inputs


def _layer_separation_outputs():
    """Core's _seedream_layer_separation_outputs, then this pack's layers_json."""
    return [
        comfy_io.Image.Output(
            display_name="base_image",
            tooltip="The base image (background plate) the layers stack onto.",
        ),
        comfy_io.Mask.Output(
            display_name="base_mask",
            tooltip=(
                "Transparency of the base image (1 = transparent, LoadImage convention); currently "
                "always fully opaque."
            ),
        ),
        comfy_io.Image.Output(
            display_name="layers",
            tooltip=(
                "Transparent layers ordered bottom to top. Full canvas mode: placed on a black "
                "base-sized canvas at their bounding-box position. Minimal size mode: cropped to "
                "their bounding box, anchored top-left, padded to the largest layer."
            ),
        ),
        comfy_io.Mask.Output(
            display_name="masks",
            tooltip=(
                "Per-layer transparency, index-aligned with the layers batch (1 = transparent, "
                "LoadImage convention). For ImageCompositeMasked-style compositing, add InvertMask first."
            ),
        ),
        comfy_io.BoundingBox.Output(
            display_name="bboxes",
            tooltip=(
                "One placement box per layer, index-aligned with the layers batch (feed both, plus "
                "masks, into Layers From Bounding Boxes to rebuild per-layer placement): {x, y, width, "
                "height, metadata: {name, desc, z_index, native_size, content_rect, flags}}. "
                "content_rect = [left, top, width, height] is the layer's content region within its "
                "own frame; it lands on the canvas at the box position plus that offset."
            ),
        ),
        comfy_io.Layers.Output(
            display_name="layer_stack",
            tooltip=(
                "Ready-to-edit layer document for Create Layered Image: the base plate plus each "
                "element as its own named, tight-cropped layer at its true position and stacking "
                "order. Connect directly, or extend with Add Layer."
            ),
        ),
        comfy_io.String.Output(
            display_name="layers_json",
            tooltip="z_index, name, description, bounding box and saved file of the base image and each layer.",
        ),
    ]


def _z_index_of(item):
    """Core's z_index_of: the layer's z_index, or _NO_Z_INDEX when missing/unreadable."""
    value = getattr(item, "z_index", None)
    if isinstance(value, bool):
        return _NO_Z_INDEX
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return _NO_Z_INDEX
    return _NO_Z_INDEX


def _downscale_to_total_pixels(image, total_pixels):
    """
    Core's downscale_image_tensor: fit (B, H, W, C) within total_pixels with even
    sides (lanczos); images that already fit are returned unchanged.
    """
    src_h, src_w = int(image.shape[1]), int(image.shape[2])
    if src_w * src_h <= total_pixels:
        return image
    scale = math.sqrt(total_pixels / (src_w * src_h))
    long_src, short_src = max(src_w, src_h), min(src_w, src_h)
    long_new = max(2, int(long_src * scale) // 2 * 2)
    short_new = max(2, math.ceil(long_new * short_src / long_src / 2) * 2)
    if long_new * short_new > total_pixels:
        long_new = max(2, total_pixels // short_new // 2 * 2)
        short_new = max(2, math.ceil(long_new * short_src / long_src / 2) * 2)
    new_w, new_h = (long_new, short_new) if src_w >= src_h else (short_new, long_new)
    samples = image.movedim(-1, 1)
    return comfy.utils.common_upscale(samples, new_w, new_h, "lanczos", "disabled").movedim(1, -1)


def split_layer_response(items):
    """
    Response items -> (base item, layer items sorted bottom to top). The base is
    the item with z_index 0 (core expects it first), else a first item without z_index.
    """
    data = [item for item in (items or []) if item is not None]
    if not data:
        raise BytePlusException(get_text("seedream_layers_err_no_base"))
    base_pos = next((i for i, item in enumerate(data) if _z_index_of(item) == 0), None)
    if base_pos is None:
        if _z_index_of(data[0]) != _NO_Z_INDEX:
            raise BytePlusException(get_text("seedream_layers_err_first_not_base"))
        base_pos = 0
    base_item = data[base_pos]
    if not getattr(base_item, "b64_json", None):
        raise BytePlusException(get_text("seedream_layers_err_no_base"))
    if getattr(base_item, "bounding_box", None) is not None:
        logger.warning(get_text("seedream_layers_warn_base_bbox"))

    others = [item for i, item in enumerate(data) if i != base_pos]
    layer_items = [item for item in others if getattr(item, "b64_json", None)]
    dropped = len(others) - len(layer_items)
    if dropped > 0:
        logger.warning(get_text("seedream_layers_warn_dropped", dropped=dropped, count=len(others)))
    if not layer_items:
        raise BytePlusException(get_text("seedream_layers_err_no_layers"))
    layer_items.sort(key=_z_index_of)
    return base_item, layer_items


def _pil_to_tensor(pil_image):
    """PIL image -> (H, W, C) float tensor in 0..1."""
    return torch.from_numpy(numpy.array(pil_image).astype(numpy.float32) / 255.0)


def assemble_layer_outputs(base_rgba, layers_rgba, layer_items, crop_layers):
    """
    Core's layer-separation output assembly. base_rgba / layers_rgba are decoded
    PIL RGBA images; layer_items the matching response items (bottom to top).
    Returns (base_image, base_mask, layers, masks, bboxes, layer_stack, flags per layer).
    """
    base_image = _pil_to_tensor(base_rgba.convert("RGB")).unsqueeze(0).contiguous()
    height, width = int(base_image.shape[1]), int(base_image.shape[2])

    specs = []
    for item in layer_items:
        flags = []
        bbox = _bbox_dict(item)
        absolute = bbox.get("absolute") if isinstance(bbox, dict) else None
        if (
            isinstance(absolute, (list, tuple))
            and len(absolute) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in absolute)
        ):
            left, top, right, bottom = (int(round(v)) for v in absolute)
            rect_w, rect_h = right - left, bottom - top  # exclusive right/bottom
            if rect_w > width or rect_h > height:
                rect_w, rect_h = min(rect_w, width), min(rect_h, height)
                flags.append("bbox_clamped")
            if rect_w <= 0 or rect_h <= 0:
                flags.append("bbox_degenerate")
        else:
            flags.append("bbox_missing")
            left, top, rect_w, rect_h = 0, 0, width, height
        specs.append(
            {"item": item, "flags": flags, "left": left, "top": top,
             "rect_w": rect_w, "rect_h": rect_h, "native_size": "", "stack_item": None}
        )

    if crop_layers:
        canvas_w = max((s["rect_w"] for s in specs if "bbox_degenerate" not in s["flags"]), default=1)
        canvas_h = max((s["rect_h"] for s in specs if "bbox_degenerate" not in s["flags"]), default=1)
    else:
        canvas_w, canvas_h = width, height
    base_mask = torch.zeros((1, height, width))
    layers = torch.zeros((len(specs), canvas_h, canvas_w, 3))
    # Create Layered Image / LoadImage mask convention: 1 = transparent
    masks = torch.ones((len(specs), canvas_h, canvas_w))

    for i, (spec, layer) in enumerate(zip(specs, layers_rgba)):
        item, flags = spec["item"], spec["flags"]
        left, top, rect_w, rect_h = spec["left"], spec["top"], spec["rect_w"], spec["rect_h"]
        rgba = _pil_to_tensor(layer.convert("RGBA"))
        spec["native_size"] = f"{rgba.shape[1]}x{rgba.shape[0]}"
        if "bbox_degenerate" in flags:
            continue
        if (rgba.shape[1], rgba.shape[0]) != (rect_w, rect_h):
            # premultiply before resizing: interpolating straight alpha bleeds the undefined
            # colors of transparent pixels into the anti-aliased edges
            rgba = rgba.clone()
            rgba[..., :3] *= rgba[..., 3:4]
            rgba = (
                torch.nn.functional.interpolate(
                    rgba.permute(2, 0, 1).unsqueeze(0),
                    size=(rect_h, rect_w),
                    mode="bilinear",
                    antialias=True,
                )
                .squeeze(0)
                .permute(1, 2, 0)
            )
            alpha = rgba[..., 3:4]
            rgba = torch.cat([rgba[..., :3] / alpha.clamp(min=1e-6), alpha], dim=-1).clamp(0, 1)
            flags.append("resized_to_bbox")
        # straight (unpremultiplied) RGB: downstream compositing applies the mask itself
        if crop_layers:
            layers[i, :rect_h, :rect_w] = rgba[..., :3]
            masks[i, :rect_h, :rect_w] = 1.0 - rgba[..., 3]
        else:
            x0, y0 = max(left, 0), max(top, 0)
            x1, y1 = min(left + rect_w, width), min(top + rect_h, height)
            if x0 < x1 and y0 < y1:
                patch = rgba[y0 - top : y1 - top, x0 - left : x1 - left]
                layers[i, y0:y1, x0:x1] = patch[..., :3]
                masks[i, y0:y1, x0:x1] = 1.0 - patch[..., 3]
            else:
                flags.append("bbox_out_of_canvas")
        z_index = _z_index_of(item)
        stack_item = {
            "image": rgba[..., :3].unsqueeze(0).contiguous(),
            "type": "raster",
            "x": left,
            "y": top,
            "z_index": z_index if z_index != _NO_Z_INDEX else i + 1,
            "mask": (1.0 - rgba[..., 3]).unsqueeze(0),
        }
        name = getattr(item, "name", None)
        if isinstance(name, str):
            stack_item["name"] = name
        spec["stack_item"] = stack_item

    stack_items = [
        {"image": base_image, "type": "raster", "x": 0, "y": 0, "z_index": 0, "name": "background"}
    ]
    boxes = []
    for i, spec in enumerate(specs):
        abnormal = [flag for flag in spec["flags"] if flag != "resized_to_bbox"]
        if abnormal:
            logger.warning(
                get_text(
                    "seedream_layers_warn_flagged",
                    index=i + 1,
                    name=repr(getattr(spec["item"], "name", None)),
                    flags=", ".join(abnormal),
                )
            )
        if spec["stack_item"] is not None:
            stack_items.append(spec["stack_item"])
        z_index = _z_index_of(spec["item"])
        # placement box sized to this layer's tensor so Create Layered Image renders it 1:1;
        # the true content rect travels in metadata, frame-relative
        rect_x, rect_y = (0, 0) if crop_layers else (spec["left"], spec["top"])
        boxes.append(
            {
                "x": spec["left"] if crop_layers else 0,
                "y": spec["top"] if crop_layers else 0,
                "width": canvas_w,
                "height": canvas_h,
                "metadata": {
                    "name": getattr(spec["item"], "name", None),
                    "desc": getattr(spec["item"], "description", None),
                    "z_index": z_index if z_index != _NO_Z_INDEX else None,
                    "native_size": spec["native_size"],
                    "content_rect": [rect_x, rect_y, max(spec["rect_w"], 0), max(spec["rect_h"], 0)],
                    "flags": spec["flags"],
                },
            }
        )
    # a single frame holding every box: the per-frame BOUNDING_BOX shape for boxes that
    # annotate one image, as emitted and consumed by CreateBoundingBoxes
    bboxes = [boxes]
    layer_stack = {"version": 1, "canvas": (width, height), "layers": stack_items}
    return base_image, base_mask, layers, masks, bboxes, layer_stack, [s["flags"] for s in specs]


def _decode_layer_images(base_item, layer_items):
    base = _b64_to_rgba_image(base_item.b64_json)
    layers = []
    for index, item in enumerate(layer_items, start=1):
        try:
            layers.append(_b64_to_rgba_image(item.b64_json))
        except Exception as exc:
            raise BytePlusException(
                get_text(
                    "seedream_layers_err_decode",
                    index=index,
                    count=len(layer_items),
                    name=repr(getattr(item, "name", None)),
                    error=exc,
                )
            ) from exc
    return base, layers


class BytePlusSeedreamLayerSeparation(comfy_io.ComfyNode):
    """
    Seedream 5.0 Pro / Flash layer separation with core's
    ByteDanceSeedreamLayerSeparationNodeV2 inputs and outputs.
    """

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusSeedreamLayerSeparation",
            display_name="BytePlus Seedream 5.0 Layer Separation",
            category=GLOBAL_CATEGORY,
            search_aliases=["layer separation", "split layers", "decompose", "cutout", "RGBA layers"],
            description=LAYER_SEPARATION_DESCRIPTION,
            inputs=[
                BytePlusClientType.Input("client"),
                comfy_io.DynamicCombo.Input(
                    "model",
                    options=[
                        comfy_io.DynamicCombo.Option(
                            SEEDREAM_PRO, _layer_separation_inputs(supports_fast=True)
                        ),
                        comfy_io.DynamicCombo.Option(
                            SEEDREAM_FLASH, _layer_separation_inputs(supports_fast=False)
                        ),
                    ],
                ),
            ],
            outputs=_layer_separation_outputs(),
            hidden=[comfy_io.Hidden.unique_id],
        )

    @classmethod
    async def execute(cls, client, model) -> comfy_io.NodeOutput:
        label = model.get("model")
        model_id = SEEDREAM_LAYER_SEPARATION_MODELS.get(label)
        if not model_id:
            raise BytePlusException(get_text("seedream_err_unknown_model", model=label))
        image = model.get("image")
        crop_layers = bool(model.get("crop_layers", False))
        count = (int(image.shape[0]) if image.ndim >= 4 else 1) if isinstance(image, torch.Tensor) else 0
        if count != 1:
            raise BytePlusException(get_text("seedream_layers_err_single_image"))
        image = image if image.ndim >= 4 else image.unsqueeze(0)
        height, width = int(image.shape[1]), int(image.shape[2])
        _check_aspect_ratio(image, "seedream_layers_err_aspect")
        if width < LAYER_MIN_SIDE or height < LAYER_MIN_SIDE:
            raise BytePlusException(
                get_text("seedream_layers_err_min_size", min=LAYER_MIN_SIDE, width=width, height=height)
            )

        output_format = model.get("output_format", "png")
        request = {
            "model": model_id,
            "prompt": str(model.get("prompt") or "").strip(),
            "image": _image_to_png_data_uri(
                _downscale_to_total_pixels(image[:, :, :, :3], LAYER_INPUT_MAX_PIXELS)
            ),
            "layer_decomposition": True,
            "size": model.get("size", "auto"),
            "output_format": output_format,
            "response_format": "b64_json",
            "watermark": bool(model.get("watermark", False)),
            "seed": int(model.get("seed", 42)),
        }
        if "prompt_optimization" in model:
            request["optimize_prompt_options"] = OptimizePromptOptions(
                mode=model["prompt_optimization"]
            )

        client.check_quota(model_id, 1)
        try:
            comfy.model_management.throw_exception_if_processing_interrupted()
            response = await asyncio.to_thread(client.ark.images.generate, **request)
            comfy.model_management.throw_exception_if_processing_interrupted()
        except comfy.model_management.InterruptProcessingException:
            raise
        except Exception as exc:
            if isinstance(exc, BytePlusException):
                raise
            raise BytePlusException(format_api_error(exc))
        error = getattr(response, "error", None)
        if error and (getattr(error, "code", None) or getattr(error, "message", None)):
            details = {"code": getattr(error, "code", None), "message": getattr(error, "message", None)}
            raise BytePlusException(format_api_error(Exception(str(details))))

        base_item, layer_items = split_layer_response(getattr(response, "data", None))
        base_rgba, layers_rgba = await asyncio.to_thread(_decode_layer_images, base_item, layer_items)
        (
            base_image, base_mask, layers, masks, bboxes, layer_stack, flags
        ) = await asyncio.to_thread(
            assemble_layer_outputs, base_rgba, layers_rgba, layer_items, crop_layers
        )
        client.update_usage(model_id, 1)

        saved = {}
        if model.get("save_layers", False):
            saved = await asyncio.to_thread(
                _save_layer_pngs,
                model.get("filename_prefix", "BytePlus/Layers/Seedream"),
                base_rgba,
                layers_rgba,
                layer_items,
                output_format,
            )
        layers_info = [
            {
                "z_index": 0,
                "name": "base",
                "description": None,
                "size": getattr(base_item, "size", None),
                "bounding_box": None,
                "file": saved.get(0),
            }
        ]
        for item, layer_flags in zip(layer_items, flags):
            z_index = _z_index_of(item)
            layers_info.append(
                {
                    "z_index": z_index if z_index != _NO_Z_INDEX else None,
                    "name": getattr(item, "name", None),
                    "description": getattr(item, "description", None),
                    "size": getattr(item, "size", None),
                    "bounding_box": _bbox_dict(item),
                    "flags": layer_flags,
                    "file": saved.get(getattr(item, "z_index", None) or 0),
                }
            )
        layers_json = json.dumps(
            {"model": getattr(response, "model", None) or model_id, "layers": layers_info},
            indent=2,
            ensure_ascii=False,
            default=_json_default,
        )
        return comfy_io.NodeOutput(
            base_image, base_mask, layers, masks, bboxes, layer_stack, layers_json
        )


# Registered in __init__.py.
NODES = [BytePlusSeedream, BytePlusSeedreamLayerSeparation]
