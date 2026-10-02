"""
Seedream helpers for nodes_seedream.py: ComfyUI core's size presets, reference
image encoding, URL-result requests and layer output.
"""
import asyncio
import base64
import io
import os

import comfy.model_management
import folder_paths
import numpy
import PIL.Image
import torch

from .nodes_shared import (
    _image_to_base64,
    get_text,
    format_api_error,
    BytePlusException,
    call_billed,
    wait_interruptible,
    safe_cat_tensors,
)
from .utils_download import (
    download_url_to_image_tensor_async,
    download_url_to_rgba_tensor_async,
)
from .constants import REF_IMAGE_MAX_SIZE_MB
from .models_config import (
    SEEDREAM_PRO,
    SEEDREAM_FLASH,
    SEEDREAM_LITE,
    SEEDREAM_4_5,
    SEEDREAM_4_0,
    SEEDREAM_MODEL_CAPS,
)


# --- BytePlus Seedream (nodes_seedream.py): ComfyUI core's size presets ---
# (label, width, height), copied from comfy_api_nodes/apis/bytedance.py.
_PRESETS_SEEDREAM_1K = [
    ("(1K) 1024x1024 (1:1)", 1024, 1024),
    ("(1K) 864x1152 (3:4)", 864, 1152),
    ("(1K) 1152x864 (4:3)", 1152, 864),
    ("(1K) 1312x736 (16:9)", 1312, 736),
    ("(1K) 736x1312 (9:16)", 736, 1312),
    ("(1K) 832x1248 (2:3)", 832, 1248),
    ("(1K) 1248x832 (3:2)", 1248, 832),
    ("(1K) 1568x672 (21:9)", 1568, 672),
]
_PRESETS_SEEDREAM_2K = [
    ("(2K) 2048x2048 (1:1)", 2048, 2048),
    ("(2K) 1728x2304 (3:4)", 1728, 2304),
    ("(2K) 2304x1728 (4:3)", 2304, 1728),
    ("(2K) 2848x1600 (16:9)", 2848, 1600),
    ("(2K) 1600x2848 (9:16)", 1600, 2848),
    ("(2K) 1664x2496 (2:3)", 1664, 2496),
    ("(2K) 2496x1664 (3:2)", 2496, 1664),
    ("(2K) 3136x1344 (21:9)", 3136, 1344),
]
_PRESETS_SEEDREAM_3K = [
    ("(3K) 3072x3072 (1:1)", 3072, 3072),
    ("(3K) 2592x3456 (3:4)", 2592, 3456),
    ("(3K) 3456x2592 (4:3)", 3456, 2592),
    ("(3K) 4096x2304 (16:9)", 4096, 2304),
    ("(3K) 2304x4096 (9:16)", 2304, 4096),
    ("(3K) 2496x3744 (2:3)", 2496, 3744),
    ("(3K) 3744x2496 (3:2)", 3744, 2496),
    ("(3K) 4704x2016 (21:9)", 4704, 2016),
]
_PRESETS_SEEDREAM_4K = [
    ("(4K) 4096x4096 (1:1)", 4096, 4096),
    ("(4K) 3520x4704 (3:4)", 3520, 4704),
    ("(4K) 4704x3520 (4:3)", 4704, 3520),
    ("(4K) 5504x3040 (16:9)", 5504, 3040),
    ("(4K) 3040x5504 (9:16)", 3040, 5504),
    ("(4K) 3328x4992 (2:3)", 3328, 4992),
    ("(4K) 4992x3328 (3:2)", 4992, 3328),
    ("(4K) 6240x2656 (21:9)", 6240, 2656),
]
# 1.5K sizes from the BytePlus docs (Seedream 5.0 Pro and Flash). Core lists
# them for Flash only; Pro gets them too, as in the ModelArk console. On Pro,
# 1.5K costs the same as 1K and gives better quality.
_PRESETS_SEEDREAM_1_5K = [
    ("(1.5K) 1536x1536 (1:1)", 1536, 1536),
    ("(1.5K) 1344x1792 (3:4)", 1344, 1792),
    ("(1.5K) 1792x1344 (4:3)", 1792, 1344),
    ("(1.5K) 2048x1152 (16:9)", 2048, 1152),
    ("(1.5K) 1152x2048 (9:16)", 1152, 2048),
    ("(1.5K) 1248x1872 (2:3)", 1248, 1872),
    ("(1.5K) 1872x1248 (3:2)", 1872, 1248),
    ("(1.5K) 2352x1008 (21:9)", 2352, 1008),
]
_PRESETS_SEEDREAM_5_FLASH = [
    ("(1K) 1024x1024 (1:1)", 1024, 1024),
    ("(1K) 864x1152 (3:4)", 864, 1152),
    ("(1K) 1152x864 (4:3)", 1152, 864),
    ("(1K) 1424x800 (16:9)", 1424, 800),
    ("(1K) 800x1424 (9:16)", 800, 1424),
    ("(1K) 832x1248 (2:3)", 832, 1248),
    ("(1K) 1248x832 (3:2)", 1248, 832),
    ("(1K) 1568x672 (21:9)", 1568, 672),
    *_PRESETS_SEEDREAM_1_5K,
    ("(2K) 2048x2048 (1:1)", 2048, 2048),
    ("(2K) 1776x2368 (3:4)", 1776, 2368),
    ("(2K) 2368x1776 (4:3)", 2368, 1776),
    ("(2K) 2816x1584 (16:9)", 2816, 1584),
    ("(2K) 1584x2816 (9:16)", 1584, 2816),
    ("(2K) 1664x2496 (2:3)", 1664, 2496),
    ("(2K) 2496x1664 (3:2)", 2496, 1664),
    ("(2K) 3136x1344 (21:9)", 3136, 1344),
]
SEEDREAM_CUSTOM_SIZE = "Custom"
SEEDREAM_PRESETS = {
    SEEDREAM_PRO: _PRESETS_SEEDREAM_1K + _PRESETS_SEEDREAM_1_5K + _PRESETS_SEEDREAM_2K,
    SEEDREAM_FLASH: _PRESETS_SEEDREAM_5_FLASH,
    SEEDREAM_LITE: _PRESETS_SEEDREAM_2K + _PRESETS_SEEDREAM_3K + _PRESETS_SEEDREAM_4K,
    SEEDREAM_4_5: _PRESETS_SEEDREAM_2K + _PRESETS_SEEDREAM_4K,
    SEEDREAM_4_0: _PRESETS_SEEDREAM_1K + _PRESETS_SEEDREAM_2K + _PRESETS_SEEDREAM_4K,
}


def seedream_adaptive_label(level):
    """Extra size preset that sends a ModelArk resolution level (e.g. "2K")."""
    return f"{level} (adaptive)"


def seedream_size_options(model):
    """
    size_preset options of a Seedream model: core's presets, then this pack's
    resolution levels ("2K (adaptive)", ...), then Custom (last, as in core).
    """
    return (
        [label for label, _, _ in SEEDREAM_PRESETS[model]]
        + [seedream_adaptive_label(level) for level in SEEDREAM_MODEL_CAPS[model]["adaptive_sizes"]]
        + [SEEDREAM_CUSTOM_SIZE]
    )


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


async def request_url_images(session, ark_client, request_kwargs, idx, model_id, transparent=False):
    """
    One Seedream request with response_format="url" (5.0 Pro / Flash): download
    every returned image. transparent keeps the alpha channel (RGBA tensor).
    Returns (tensor, metadata) for BytePlusGenerationExecutor.run_parallel_requests.
    """
    download = (
        download_url_to_rgba_tensor_async
        if transparent
        else download_url_to_image_tensor_async
    )
    try:
        comfy.model_management.throw_exception_if_processing_interrupted()
        response = await wait_interruptible(asyncio.to_thread(call_billed, ark_client.images.generate, **request_kwargs))
        comfy.model_management.throw_exception_if_processing_interrupted()

        urls = [
            getattr(item, "url", None)
            for item in (getattr(response, "data", None) or [])
        ]
        urls = [url for url in urls if url]
        if not urls:
            raise BytePlusException(get_text("err_download_img"))

        downloaded = await asyncio.gather(*[download(session, url) for url in urls])
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


def _b64_to_rgba_image(b64_data):
    return PIL.Image.open(io.BytesIO(base64.b64decode(b64_data))).convert("RGBA")


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
