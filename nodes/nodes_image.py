import random
import asyncio
import aiohttp
import torch
import comfy.model_management
import base64
import io
import json
import os
import time

import numpy
import PIL.Image
import folder_paths

from comfy_api.latest import io as comfy_io

from byteplussdkarkruntime.types.images.images import (
    SequentialImageGenerationOptions,
    OptimizePromptOptions,
)

from .nodes_shared import (
    GLOBAL_CATEGORY,
    _image_to_base64,
    get_text,
    log_msg,
    format_api_error,
    BytePlusClientType,
    BytePlusException,
    get_node_count_in_workflow,
    create_white_image_tensor,
    safe_cat_tensors,
)
from .utils_download import (
    download_url_to_image_tensor_async,
    download_url_to_rgba_tensor_async,
)
from .executor import BytePlusGenerationExecutor

from .models_config import (
    SEEDREAM_4_MODEL_MAP,
    SEEDREAM_4_0_UI_MODEL,
    SEEDREAM_5_MODEL_MAP,
    SEEDREAM_5_PRO_UI_MODEL,
    SEEDREAM_5_FLASH_UI_MODEL,
    SEEDREAM_5_URL_MODELS,
    SEEDREAM_LAYER_MODEL_MAP,
    SEEDREAM_LAYER_SIZES,
    PROMPT_OPTIMIZATION_MODES,
)
from .constants import (
    MAX_SEED,
    MIN_SEED,
    MAX_GENERATION_COUNT,
    MIN_IMAGE_PIXELS_V4_5,
    MAX_IMAGE_PIXELS_V4,
    MIN_IMAGE_PIXELS_V5,
    MAX_IMAGE_PIXELS_V5,
    MIN_IMAGE_PIXELS_V5_PRO,
    MAX_IMAGE_PIXELS_V5_PRO,
    MIN_ASPECT_RATIO,
    MAX_ASPECT_RATIO,
    MIN_LAYER_INPUT_PIXELS,
    MAX_LAYER_INPUT_PIXELS,
    REF_IMAGE_MAX_SIZE_MB,
)
from .nodes_image_schema import (
    RECOMMENDED_SIZES_V4,
    RECOMMENDED_SIZES_V5,
    RECOMMENDED_SIZES_V5_PRO,
    get_image_generation_inputs,
)

def resolve_seedream5_pro_size(size: str, width: int, height: int) -> str:
    """
    Seedream 5.0 Pro and Flash accept a resolution level (1K / 1.5K / 2K) or WxH
    within [1280x720, 2048x2048x1.1025] total pixels.
    """
    if size == "Custom":
        return validate_custom_size(
            width, height, MIN_IMAGE_PIXELS_V5_PRO, MAX_IMAGE_PIXELS_V5_PRO
        )
    return (size or "2K").split(" ")[0]


def _mask_to_alpha(mask, height, width):
    """ComfyUI MASK (1 = transparent) -> alpha array (1 = opaque) of the image size."""
    mask = mask if mask.ndim == 3 else mask.unsqueeze(0)
    mask = mask[:1].float()
    if mask.shape[-2:] != (height, width):
        mask = torch.nn.functional.interpolate(
            mask.unsqueeze(1), size=(height, width), mode="bilinear", align_corners=False
        ).squeeze(1)
    return 1.0 - mask[0].clamp(0.0, 1.0).cpu().numpy()


def _image_to_png_data_uri(image, mask=None, with_alpha=False):
    """
    Encode one image as a PNG data URI. With a ComfyUI mask, or with_alpha,
    the PNG gets an alpha channel (fully opaque when there is no mask).
    """
    rgb = numpy.clip(image[0].cpu().numpy() * 255.0, 0, 255).astype(numpy.uint8)
    pil_image = PIL.Image.fromarray(rgb, "RGB")
    if mask is not None:
        alpha = _mask_to_alpha(mask, rgb.shape[0], rgb.shape[1])
        pil_image.putalpha(PIL.Image.fromarray((alpha * 255.0).astype(numpy.uint8), "L"))
    elif with_alpha:
        pil_image.putalpha(255)
    buffer = io.BytesIO()
    pil_image.save(buffer, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("utf-8")
    size_mb = len(data_uri) / (1024.0 * 1024.0)
    if size_mb > REF_IMAGE_MAX_SIZE_MB:
        raise BytePlusException(
            get_text("popup_ref_image_size_exceeded").format(
                max_mb=REF_IMAGE_MAX_SIZE_MB, size_mb=f"{size_mb:.3f}"
            )
        )
    return data_uri


def _split_rgba(tensor):
    """(B, H, W, 3|4) -> RGB image and ComfyUI mask (1 = transparent)."""
    if tensor.shape[-1] == 4:
        return tensor[..., :3].contiguous(), 1.0 - tensor[..., 3].contiguous()
    return tensor, torch.zeros(tensor.shape[:3], dtype=tensor.dtype)


def validate_custom_size(width, height, min_pixels, max_pixels):
    """
    Check a custom width/height against the model's pixel and aspect-ratio limits.
    """
    total_pixels = width * height
    if not (min_pixels <= total_pixels <= max_pixels):
        raise BytePlusException(
            get_text("err_pixels_range").format(
                min=min_pixels,
                max=max_pixels,
                current=total_pixels,
            )
        )

    aspect_ratio = width / height
    if not (MIN_ASPECT_RATIO <= aspect_ratio <= MAX_ASPECT_RATIO):
        raise BytePlusException(
            get_text("err_aspect_ratio").format(
                min="1/16", max="16", current=aspect_ratio
            )
        )
    return f"{width}x{height}"


def _get_dynamic_input_order(name):
    match = None
    try:
        import re
        match = re.search(r"(\d+)$", str(name))
    except Exception:
        match = None
    if match:
        return int(match.group(1))
    return 999


def _create_seedream_autogrow_input():
    return comfy_io.Autogrow.Input(
        "images",
        template=comfy_io.Autogrow.TemplateNames(
            input=comfy_io.Image.Input("image", optional=True),
            names=[f"image_{idx}" for idx in range(1, 15)],
            min=1,
        ),
    )


def _collect_image_tensors(images=None, **kwargs):
    """Connected reference images as a list of (1, H, W, 3) tensors, in input order."""
    tensors = []
    if isinstance(images, dict):
        for _, tensor in sorted(images.items(), key=lambda item: _get_dynamic_input_order(item[0])):
            if isinstance(tensor, torch.Tensor):
                tensors.append(tensor)
    elif isinstance(images, torch.Tensor):
        tensors.append(images)
    for key in sorted([k for k in kwargs if k.startswith("image_")], key=_get_dynamic_input_order):
        if isinstance(kwargs[key], torch.Tensor):
            tensors.append(kwargs[key])
    return [tensor[i : i + 1] for tensor in tensors for i in range(tensor.shape[0])]


def _prepare_multi_image_inputs(images=None, **kwargs):
    input_image_tensors = []

    if isinstance(images, dict):
        sorted_items = sorted(images.items(), key=lambda item: _get_dynamic_input_order(item[0]))
        for _, image_tensor in sorted_items:
            if isinstance(image_tensor, torch.Tensor):
                input_image_tensors.append(image_tensor)
    elif isinstance(images, torch.Tensor):
        input_image_tensors.append(images)

    for key in sorted([k for k in kwargs.keys() if k.startswith("image_")], key=_get_dynamic_input_order):
        image_tensor = kwargs[key]
        if isinstance(image_tensor, torch.Tensor):
            input_image_tensors.append(image_tensor)

    n_input_images = 0
    image_b64_list = []
    for tensor in input_image_tensors:
        batch_size = tensor.shape[0]
        n_input_images += batch_size
        for i in range(batch_size):
            image_b64_list.append(_image_to_base64(tensor[i : i + 1]))

    image_param = None
    if n_input_images > 0:
        if n_input_images == 1:
            image_param = f"data:image/jpeg;base64,{image_b64_list[0]}"
        else:
            image_param = [
                f"data:image/jpeg;base64,{image_b64}" for image_b64 in image_b64_list
            ]

    return n_input_images, image_param


class BytePlusSeedream4(comfy_io.ComfyNode):
    """
    Seedream 4.0 / 4.5 image generation node.
    Text-to-image, image editing and group generation, received via streaming.
    """
    RECOMMENDED_SIZES = RECOMMENDED_SIZES_V4

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        generation_inputs = get_image_generation_inputs(
            cls.RECOMMENDED_SIZES,
            default_width=2048,
            default_height=2048,
            enable_group_generation=True,
        )
        generation_inputs.insert(
            len(generation_inputs) - 1,
            comfy_io.Combo.Input(
                "prompt_optimization",
                options=PROMPT_OPTIMIZATION_MODES,
                default="standard",
                tooltip=(
                    "Prompt optimization mode for Seedream 4.0: standard (higher "
                    "quality) or fast (lower latency). Seedream 4.5 always uses standard."
                ),
            ),
        )
        return comfy_io.Schema(
            node_id="BytePlusSeedream4",
            display_name="BytePlus Seedream 4",
            category=GLOBAL_CATEGORY,
            inputs=[
                BytePlusClientType.Input("client"),
                comfy_io.Combo.Input(
                    "model_version", options=list(SEEDREAM_4_MODEL_MAP.keys())
                ),
                comfy_io.String.Input("prompt", multiline=True, default=""),
            ]
            + generation_inputs
            + [_create_seedream_autogrow_input()],
            hidden=[comfy_io.Hidden.unique_id, comfy_io.Hidden.prompt],
            outputs=[
                comfy_io.Image.Output(display_name="images"),
                comfy_io.String.Output(display_name="response"),
            ],
        )

    @classmethod
    async def execute(
        cls,
        client,
        model_version,
        prompt,
        enable_group_generation,
        max_images,
        size,
        width,
        height,
        seed,
        generation_count,
        watermark,
        prompt_optimization="standard",
        images=None,
        **kwargs,
    ) -> comfy_io.NodeOutput:
        node_id = cls.hidden.unique_id
        ark_client = client.ark

        model_id = SEEDREAM_4_MODEL_MAP.get(model_version)
        if not model_id:
            model_id = list(SEEDREAM_4_MODEL_MAP.values())[0]

        sequential_param = "auto" if enable_group_generation else "disabled"
        n_input_images, image_param = _prepare_multi_image_inputs(images, **kwargs)

        if sequential_param == "auto":
            total_count = n_input_images + max_images
            if total_count > 15:
                raise BytePlusException(
                    get_text("err_img_limit_group_15").format(
                        n=n_input_images, max=max_images, total=total_count
                    )
                )

        if size == "Custom":
            min_pixels = 1280 * 720

            if model_version != SEEDREAM_4_0_UI_MODEL:
                min_pixels = MIN_IMAGE_PIXELS_V4_5

            size_str = validate_custom_size(
                width,
                height,
                min_pixels,
                MAX_IMAGE_PIXELS_V4,
            )
        else:
            size_str = size.split(" ")[0]

        seq_options = None
        if sequential_param == "auto":
            seq_options = SequentialImageGenerationOptions(max_images=max_images)

        client.check_quota(model_id, generation_count * max_images if enable_group_generation else generation_count)

        if generation_count > 1:
            log_msg("batch_submit_start", count=generation_count, model=model_id)

        node_count = get_node_count_in_workflow("BytePlusSeedream4", prompt=cls.hidden.prompt)
        # log_msg("debug_node_count", count=node_count, type="BytePlusSeedream4")
        ignore_errors = node_count > 1

        executor = BytePlusGenerationExecutor(client, node_id, ignore_errors=ignore_errors)

        async def _generate_single(idx, session):
            current_seed = random.randint(0, MAX_SEED) if seed == -1 else seed + idx
            
            kwargs = {
                "model": model_id,
                "prompt": prompt,
                "size": size_str,
                "response_format": "b64_json",
                "watermark": watermark,
                "seed": current_seed,
                "sequential_image_generation": sequential_param,
            }
            if image_param:
                kwargs["image"] = image_param
            if seq_options:
                kwargs["sequential_image_generation_options"] = seq_options
            if model_version == SEEDREAM_4_0_UI_MODEL:
                kwargs["optimize_prompt_options"] = OptimizePromptOptions(
                    mode=prompt_optimization
                )
                
            return await executor.stream_generation_helper(
                session, ark_client, kwargs, idx, enable_group_generation, generation_count
            )

        tensors, metadata = await executor.run_parallel_requests(generation_count, _generate_single)

        if tensors:
            try:
                total_imgs = sum([t.shape[0] for t in tensors]) if isinstance(tensors, list) else tensors.shape[0]
                client.update_usage(model_id, total_imgs)
            except:
                pass
        
        if not tensors:
             return comfy_io.NodeOutput(create_white_image_tensor(), "[]")

        output_tensor = safe_cat_tensors(tensors)
        
        return comfy_io.NodeOutput(
            output_tensor, json.dumps(metadata, indent=2)
        )

class BytePlusSeedream5(comfy_io.ComfyNode):
    """
    Seedream 5.0 Pro / Flash / Lite image generation node.
    Pro and Flash return URLs; Lite supports group generation via streaming.
    """
    RECOMMENDED_SIZES = RECOMMENDED_SIZES_V5

    @staticmethod
    def _model_inputs(model_version):
        if model_version in SEEDREAM_5_URL_MODELS:
            inputs = [
                comfy_io.String.Input("prompt", multiline=True, default=""),
                *get_image_generation_inputs(
                    RECOMMENDED_SIZES_V5_PRO,
                    default_width=2048,
                    default_height=2048,
                )[:-2],
                comfy_io.Int.Input(
                    "generation_count", default=1, min=1, max=MAX_GENERATION_COUNT
                ),
            ]
            if model_version == SEEDREAM_5_PRO_UI_MODEL:
                inputs.append(
                    comfy_io.Combo.Input(
                        "prompt_optimization",
                        options=PROMPT_OPTIMIZATION_MODES,
                        default="standard",
                        tooltip="standard: higher quality. fast: lower latency.",
                    )
                )
            inputs.extend(
                [
                    comfy_io.Combo.Input("output_format", options=["jpeg", "png"], default="jpeg"),
                    comfy_io.Combo.Input(
                        "background",
                        options=["opaque", "transparent"],
                        default="opaque",
                        tooltip=(
                            "transparent: edit one reference image that has an alpha "
                            "channel (connect its mask to reference_mask) and return a "
                            "PNG with transparency. Needs output_format png."
                        ),
                    ),
                    comfy_io.Boolean.Input("watermark", default=False),
                ]
            )
            return inputs

        return [
            comfy_io.String.Input("prompt", multiline=True, default=""),
            *get_image_generation_inputs(
                RECOMMENDED_SIZES_V5,
                default_width=2048,
                default_height=2048,
                enable_group_generation=True,
            ),
        ]

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusSeedream5",
            display_name="BytePlus Seedream 5",
            category=GLOBAL_CATEGORY,
            description=(
                "Generate images with Seedream 5.0 Pro, Flash or Lite. Pro supports "
                "standard and fast prompt optimization; Pro and Flash support PNG "
                "output and transparent backgrounds; Lite supports grouped generation."
            ),
            inputs=[
                BytePlusClientType.Input("client"),
                comfy_io.DynamicCombo.Input(
                    "model_version",
                    options=[
                        comfy_io.DynamicCombo.Option(
                            model_version, cls._model_inputs(model_version)
                        )
                        for model_version in SEEDREAM_5_MODEL_MAP
                    ],
                ),
                _create_seedream_autogrow_input(),
                comfy_io.Mask.Input(
                    "reference_mask",
                    optional=True,
                    tooltip=(
                        "Alpha of the reference image for transparent background "
                        "(connect the MASK output of Load Image)."
                    ),
                ),
            ],
            hidden=[comfy_io.Hidden.unique_id, comfy_io.Hidden.prompt],
            outputs=[
                comfy_io.Image.Output(display_name="images"),
                comfy_io.String.Output(display_name="response"),
                comfy_io.Mask.Output(
                    display_name="mask",
                    tooltip=(
                        "Transparency of the output (1 = transparent, like Load "
                        "Image). Empty unless background is transparent."
                    ),
                ),
            ],
        )

    @classmethod
    async def execute(
        cls,
        client,
        model_version,
        prompt="",
        enable_group_generation=False,
        max_images=1,
        size="2K (adaptive)",
        width=2048,
        height=2048,
        seed=0,
        generation_count=1,
        watermark=False,
        prompt_optimization="standard",
        output_format="jpeg",
        background="opaque",
        images=None,
        reference_mask=None,
        **kwargs,
    ) -> comfy_io.NodeOutput:
        node_id = cls.hidden.unique_id
        ark_client = client.ark

        if isinstance(model_version, dict):
            model_config = model_version
            model_version = model_config.get("model_version", SEEDREAM_5_PRO_UI_MODEL)
            prompt = model_config.get("prompt", prompt)
            enable_group_generation = model_config.get(
                "enable_group_generation", enable_group_generation
            )
            max_images = model_config.get("max_images", max_images)
            size = model_config.get("size", size)
            width = model_config.get("width", width)
            height = model_config.get("height", height)
            seed = model_config.get("seed", seed)
            generation_count = model_config.get("generation_count", generation_count)
            watermark = model_config.get("watermark", watermark)
            prompt_optimization = model_config.get("prompt_optimization", prompt_optimization)
            output_format = model_config.get("output_format", output_format)
            background = model_config.get("background", background)

        model_id = SEEDREAM_5_MODEL_MAP.get(model_version)
        if not model_id:
            raise BytePlusException(get_text("err_model_not_supported").format(model=model_version))

        is_url_model = model_version in SEEDREAM_5_URL_MODELS
        if model_version == SEEDREAM_5_FLASH_UI_MODEL and prompt_optimization != "standard":
            raise BytePlusException(get_text("err_seedream_flash_prompt_optimization"))

        sequential_param = "auto" if enable_group_generation and not is_url_model else "disabled"
        n_input_images, image_param = _prepare_multi_image_inputs(images, **kwargs)

        if is_url_model and n_input_images > 10:
            raise BytePlusException(get_text("err_img_limit_10"))
        transparent = is_url_model and background == "transparent"
        if transparent:
            if n_input_images != 1:
                raise BytePlusException(
                    get_text("err_transparent_needs_one_image", n=n_input_images)
                )
            if output_format != "png":
                raise BytePlusException(get_text("err_transparent_needs_png"))
            # The API needs an input with an alpha channel; without a mask
            # the image is sent fully opaque.
            image_param = _image_to_png_data_uri(
                _collect_image_tensors(images, **kwargs)[0], reference_mask, with_alpha=True
            )

        if sequential_param == "auto":
            total_count = n_input_images + max_images
            if total_count > 15:
                raise BytePlusException(
                    get_text("err_img_limit_group_15").format(
                        n=n_input_images, max=max_images, total=total_count
                    )
                )

        if is_url_model:
            size_str = resolve_seedream5_pro_size(size, width, height)
        elif size == "Custom":
            min_pixels = MIN_IMAGE_PIXELS_V5

            size_str = validate_custom_size(
                width,
                height,
                min_pixels,
                MAX_IMAGE_PIXELS_V5,
            )
        else:
            size_str = size.split(" ")[0]

        seq_options = None
        if sequential_param == "auto":
            seq_options = SequentialImageGenerationOptions(max_images=max_images)

        client.check_quota(
            model_id,
            generation_count * max_images
            if enable_group_generation and not is_url_model
            else generation_count,
        )

        if generation_count > 1:
            log_msg("batch_submit_start", count=generation_count, model=model_id)
        
        node_count = get_node_count_in_workflow("BytePlusSeedream5", prompt=cls.hidden.prompt)
        # log_msg("debug_node_count", count=node_count, type="BytePlusSeedream5")
        ignore_errors = node_count > 1

        executor = BytePlusGenerationExecutor(client, node_id, ignore_errors=ignore_errors)

        async def _generate_single(idx, session):
            current_seed = random.randint(0, MAX_SEED) if seed == -1 else seed + idx

            if is_url_model:
                request_kwargs = {
                    "model": model_id,
                    "prompt": prompt,
                    "size": size_str,
                    "response_format": "url",
                    "watermark": watermark,
                    "seed": current_seed,
                    "output_format": output_format,
                }
                if model_version == SEEDREAM_5_PRO_UI_MODEL:
                    request_kwargs["optimize_prompt_options"] = OptimizePromptOptions(
                        mode=prompt_optimization
                    )
                if image_param:
                    request_kwargs["image"] = image_param
                if transparent:
                    request_kwargs["extra_body"] = {"background": "transparent"}
                download = (
                    download_url_to_rgba_tensor_async
                    if transparent
                    else download_url_to_image_tensor_async
                )

                try:
                    comfy.model_management.throw_exception_if_processing_interrupted()
                    response = await asyncio.to_thread(
                        ark_client.images.generate, **request_kwargs
                    )
                    comfy.model_management.throw_exception_if_processing_interrupted()

                    urls = [
                        getattr(item, "url", None)
                        for item in (getattr(response, "data", None) or [])
                    ]
                    urls = [url for url in urls if url]
                    if not urls:
                        raise BytePlusException(get_text("err_download_img"))

                    downloaded = await asyncio.gather(
                        *[download(session, url) for url in urls]
                    )
                    downloaded = [tensor for tensor in downloaded if tensor is not None]
                    if not downloaded:
                        raise BytePlusException(get_text("err_download_img"))

                    metadata = {
                        "batch_index": idx,
                        "model": getattr(response, "model", model_id),
                        "created": getattr(response, "created", None),
                        "urls": urls,
                    }
                    return safe_cat_tensors(downloaded), metadata
                except comfy.model_management.InterruptProcessingException:
                    raise
                except Exception as exc:
                    if isinstance(exc, BytePlusException):
                        raise
                    raise BytePlusException(format_api_error(exc))
            
            request_kwargs = {
                "model": model_id,
                "prompt": prompt,
                "size": size_str,
                "response_format": "b64_json",
                "watermark": watermark,
                "seed": current_seed,
                "sequential_image_generation": sequential_param,
            }
            if image_param:
                request_kwargs["image"] = image_param
            if seq_options:
                request_kwargs["sequential_image_generation_options"] = seq_options
                
            return await executor.stream_generation_helper(
                session,
                ark_client,
                request_kwargs,
                idx,
                enable_group_generation,
                generation_count,
            )

        tensors, metadata = await executor.run_parallel_requests(generation_count, _generate_single)

        if tensors:
            try:
                total_imgs = sum([t.shape[0] for t in tensors]) if isinstance(tensors, list) else tensors.shape[0]
                client.update_usage(model_id, total_imgs)
            except:
                pass
        
        if not tensors:
            empty = create_white_image_tensor()
            return comfy_io.NodeOutput(empty, "[]", torch.zeros(empty.shape[:3]))

        output_tensor, output_mask = _split_rgba(safe_cat_tensors(tensors))

        return comfy_io.NodeOutput(
            output_tensor, json.dumps(metadata, indent=2), output_mask
        )


def _b64_to_rgba_image(b64_data):
    return PIL.Image.open(io.BytesIO(base64.b64decode(b64_data))).convert("RGBA")


def _rgba_images_to_tensors(rgba_images):
    """List of same-size PIL RGBA images -> (N, H, W, 3) image and (N, H, W) ComfyUI mask."""
    stacked = numpy.stack(
        [numpy.array(img).astype(numpy.float32) / 255.0 for img in rgba_images]
    )
    tensor = torch.from_numpy(stacked)
    return tensor[..., :3].contiguous(), 1.0 - tensor[..., 3].contiguous()


def place_layer_on_canvas(layer, bounding_box, canvas_size):
    """
    Scale a layer to its bounding box and place it on a transparent canvas the
    size of the base image, as described by bounding_box.absolute.
    """
    canvas = PIL.Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    absolute = (bounding_box or {}).get("absolute")
    if absolute and len(absolute) == 4:
        left, top, right, bottom = [int(v) for v in absolute]
        size = (max(1, right - left), max(1, bottom - top))
        position = (left, top)
    else:
        size, position = canvas_size, (0, 0)
    # Plain paste copies RGBA as-is (clipped to the canvas); a masked paste
    # would blend the alpha channel with the empty canvas.
    canvas.paste(layer.resize(size, PIL.Image.LANCZOS), position)
    return canvas


class BytePlusSeedreamLayers(comfy_io.ComfyNode):
    """
    Seedream 5.0 Pro / Flash layer decomposition: splits one image into a base image
    and up to 16 transparent layers (subjects, background, text, ...).
    """

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusSeedreamLayers",
            display_name="BytePlus Seedream Layer Decomposition",
            category=GLOBAL_CATEGORY,
            # Runs even with nothing connected, so save_layers alone is useful.
            is_output_node=True,
            description=(
                "Split an image into a base image and up to 16 editable layers with "
                "Seedream 5.0 Pro or Flash. Layers are returned placed on the base image canvas, "
                "with their masks; the original layer PNGs can be saved to the output folder."
            ),
            inputs=[
                BytePlusClientType.Input("client"),
                comfy_io.Combo.Input("model", options=list(SEEDREAM_LAYER_MODEL_MAP.keys())),
                comfy_io.Image.Input("image"),
                comfy_io.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip="Optional guidance, e.g. which elements to separate.",
                ),
                comfy_io.Combo.Input(
                    "size",
                    options=SEEDREAM_LAYER_SIZES,
                    default="auto",
                    tooltip="Base image resolution. auto keeps the input's dimensions within 1K-2K.",
                ),
                comfy_io.Combo.Input(
                    "output_format",
                    options=["png", "jpeg"],
                    default="png",
                    tooltip="Format of the base image. Layers are always PNG.",
                ),
                comfy_io.Int.Input("seed", default=0, min=MIN_SEED, max=MAX_SEED),
                comfy_io.Boolean.Input("watermark", default=False),
                comfy_io.Boolean.Input(
                    "save_layers",
                    default=True,
                    tooltip="Save the base image and original layer PNGs to the output folder.",
                ),
                comfy_io.String.Input("filename_prefix", default="BytePlus/Layers/Seedream"),
            ],
            hidden=[comfy_io.Hidden.unique_id],
            outputs=[
                comfy_io.Image.Output(display_name="base_image"),
                comfy_io.Image.Output(
                    display_name="layers",
                    tooltip="One image per layer, placed on the base image canvas, in z order.",
                ),
                comfy_io.Mask.Output(
                    display_name="layer_masks",
                    tooltip="Per-layer transparency (1 = transparent, like Load Image).",
                ),
                comfy_io.String.Output(
                    display_name="layers_json",
                    tooltip="z_index, name, description and bounding box of each layer.",
                ),
            ],
        )

    @classmethod
    async def execute(
        cls,
        client,
        model,
        image,
        prompt="",
        size="auto",
        output_format="png",
        seed=0,
        watermark=False,
        save_layers=True,
        filename_prefix="BytePlus/Layers/Seedream",
    ) -> comfy_io.NodeOutput:
        model_id = SEEDREAM_LAYER_MODEL_MAP.get(model)
        if not model_id:
            raise BytePlusException(get_text("err_model_not_supported").format(model=model))

        height, width = int(image.shape[1]), int(image.shape[2])
        if not (MIN_LAYER_INPUT_PIXELS <= width * height <= MAX_LAYER_INPUT_PIXELS):
            raise BytePlusException(
                get_text(
                    "err_layer_input_pixels",
                    min=MIN_LAYER_INPUT_PIXELS,
                    max=MAX_LAYER_INPUT_PIXELS,
                    current=width * height,
                )
            )

        client.check_quota(model_id, 1)
        request_kwargs = {
            "model": model_id,
            "prompt": prompt or "",
            "image": _image_to_png_data_uri(image[:1]),
            "layer_decomposition": True,
            "size": size,
            "output_format": output_format,
            "response_format": "b64_json",
            "watermark": watermark,
            "seed": seed,
        }
        try:
            comfy.model_management.throw_exception_if_processing_interrupted()
            response = await asyncio.to_thread(client.ark.images.generate, **request_kwargs)
        except comfy.model_management.InterruptProcessingException:
            raise
        except Exception as exc:
            if isinstance(exc, BytePlusException):
                raise
            raise BytePlusException(format_api_error(exc))

        items = [item for item in (getattr(response, "data", None) or []) if getattr(item, "b64_json", None)]
        if not items:
            raise BytePlusException(get_text("err_layer_decomposition_empty"))
        items.sort(key=lambda item: getattr(item, "z_index", None) or 0)

        base_item, layer_items = items[0], items[1:]
        base = _b64_to_rgba_image(base_item.b64_json)
        layers = [_b64_to_rgba_image(item.b64_json) for item in layer_items]
        placed = [
            place_layer_on_canvas(layer, _bbox_dict(item), base.size)
            for layer, item in zip(layers, layer_items)
        ]

        saved = {}
        if save_layers:
            saved = await asyncio.to_thread(
                _save_layer_pngs, filename_prefix, base, layers, layer_items, output_format
            )

        layers_info = []
        for item in items:
            z_index = getattr(item, "z_index", None) or 0
            layers_info.append(
                {
                    "z_index": z_index,
                    "name": getattr(item, "name", None) or ("base" if item is base_item else None),
                    "description": getattr(item, "description", None),
                    "size": getattr(item, "size", None),
                    "bounding_box": _bbox_dict(item),
                    "file": saved.get(z_index),
                }
            )

        client.update_usage(model_id, 1)
        base_image, _ = _rgba_images_to_tensors([base])
        if placed:
            layer_images, layer_masks = _rgba_images_to_tensors(placed)
        else:
            layer_images = base_image
            layer_masks = torch.ones(base_image.shape[:3])
        return comfy_io.NodeOutput(
            base_image,
            layer_images,
            layer_masks,
            json.dumps(
                {"model": getattr(response, "model", model_id), "layers": layers_info},
                indent=2,
                ensure_ascii=False,
            ),
        )


def _bbox_dict(item):
    bounding_box = getattr(item, "bounding_box", None)
    if bounding_box is None:
        return None
    if isinstance(bounding_box, dict):
        return bounding_box
    return {
        "absolute": getattr(bounding_box, "absolute", None),
        "normalized": getattr(bounding_box, "normalized", None),
    }


def _save_layer_pngs(filename_prefix, base, layers, layer_items, output_format):
    """Save the base image and the original layer PNGs; return {z_index: relative path}."""
    output_dir = folder_paths.get_output_directory()
    full_folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
        filename_prefix, output_dir, base.width, base.height
    )
    os.makedirs(full_folder, exist_ok=True)
    saved = {}
    stem = f"{filename}_{counter:05}"
    base_ext = "png" if output_format == "png" else "jpg"
    base_name = f"{stem}_base.{base_ext}"
    (base if base_ext == "png" else base.convert("RGB")).save(os.path.join(full_folder, base_name))
    saved[0] = os.path.join(subfolder, base_name)
    for layer, item in zip(layers, layer_items):
        z_index = getattr(item, "z_index", None) or 0
        layer_name = f"{stem}_layer{z_index:02d}.png"
        layer.save(os.path.join(full_folder, layer_name))
        saved[z_index] = os.path.join(subfolder, layer_name)
    return saved
