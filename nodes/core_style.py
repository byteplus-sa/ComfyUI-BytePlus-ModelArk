"""
Shared pieces for nodes shaped like ComfyUI core's ByteDance partner nodes
(comfy_api_nodes/nodes_bytedance.py): the same seed/watermark widgets, this
pack's extras as advanced inputs, the linked-output check, and resolving
reference strings (asset IDs, asset:// URIs, https links) for Seedance.
"""
import asyncio
import os
from urllib.parse import urlparse

import aiohttp
from comfy_api.latest import io as comfy_io

from .constants import ASSET_URI_PREFIX, DEFAULT_FILENAME_PREFIX
from .models_config import MODEL_REGION_EXCLUSIONS, RETIRED_MODELS
from .nodes_shared import BytePlusException, get_text

SEED_MAX = 2147483647
SEEDANCE_SEED_TOOLTIP = (
    "Seed controls whether the node should re-run; "
    "results are non-deterministic regardless of seed."
)

REFERENCE_KINDS = ("image", "video", "audio")
_URL_EXTENSION_KINDS = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image",
    ".bmp": "image", ".tif": "image", ".tiff": "image", ".gif": "image",
    ".heic": "image", ".heif": "image",
    ".mp4": "video", ".mov": "video",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".aac": "audio",
    ".ogg": "audio", ".flac": "audio",
}
_ASSET_TYPE_KINDS = {"Image": "image", "Video": "video", "Audio": "audio"}
_URL_PROBE_TIMEOUT_SECONDS = 15


def seed_input(default=0, tooltip=SEEDANCE_SEED_TOOLTIP, optional=False):
    """Core's seed widget: 0..2^31-1 with control_after_generate."""
    return comfy_io.Int.Input(
        "seed",
        default=default,
        min=0,
        max=SEED_MAX,
        step=1,
        display_mode=comfy_io.NumberDisplay.number,
        control_after_generate=True,
        tooltip=tooltip,
        optional=optional,
    )


def watermark_input(tooltip="Whether to add a watermark to the video.", optional=False):
    return comfy_io.Boolean.Input(
        "watermark",
        default=False,
        tooltip=tooltip,
        advanced=True,
        optional=optional,
    )


def generation_count_input():
    return comfy_io.Int.Input(
        "generation_count",
        default=1,
        min=1,
        tooltip="Number of separate generations to run in parallel.",
        advanced=True,
    )


def filename_prefix_input():
    return comfy_io.String.Input(
        "filename_prefix",
        default=DEFAULT_FILENAME_PREFIX,
        tooltip="Results are also saved to the output folder with this prefix.",
        advanced=True,
    )


def video_extra_inputs(include_offline=False):
    """This pack's video extras, placed after core's inputs."""
    inputs = []
    if include_offline:
        inputs.append(
            comfy_io.Boolean.Input(
                "enable_offline_inference",
                default=False,
                tooltip="Use the flex (offline) service tier: lower price, results within 48 hours.",
                advanced=True,
            )
        )
    inputs.extend(
        [
            generation_count_input(),
            filename_prefix_input(),
            comfy_io.Boolean.Input(
                "save_last_frame_batch",
                default=False,
                tooltip="Also save the last frame of every generated video.",
                advanced=True,
            ),
            comfy_io.Boolean.Input(
                "non_blocking",
                default=False,
                tooltip=(
                    "Submit the task and return at once; run the node again to "
                    "collect the finished video."
                ),
                advanced=True,
            ),
        ]
    )
    return inputs


def raise_if_model_retired(model):
    """Legacy nodes keep retired models in their lists so saved workflows load."""
    retired = RETIRED_MODELS.get(str(model or ""))
    if retired:
        model_id, replacement = retired
        raise BytePlusException(get_text("err_model_retired", model=model_id, replacement=replacement))


def raise_if_model_unavailable_in_region(client, model_id):
    """Some models are not offered in every region (see MODEL_REGION_EXCLUSIONS)."""
    region = str(getattr(client, "region", "") or "")
    if region in MODEL_REGION_EXCLUSIONS.get(model_id, ()):
        raise BytePlusException(get_text("err_model_region_unavailable", model=model_id, region=region))


def get_output_consumers(prompt, node_id, output_index):
    """Nodes in the queued prompt that read output `output_index` of `node_id`."""
    consumers = []
    node_id = str(node_id)
    for consumer_id, node in (prompt or {}).items():
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            continue
        for value in inputs.values():
            if (
                isinstance(value, (list, tuple))
                and len(value) == 2
                and str(value[0]) == node_id
                and value[1] == output_index
            ):
                consumers.append(f"{node.get('class_type', '?')} #{consumer_id}")
                break
    return consumers


def raise_if_output_linked(cls, output_index, reason):
    """Core's validate_output_unlinked, using the queued prompt."""
    consumers = get_output_consumers(
        getattr(cls.hidden, "prompt", None), cls.hidden.unique_id, output_index
    )
    if consumers:
        raise BytePlusException(
            get_text("err_output_linked", reason=reason, consumers=", ".join(consumers))
        )


def split_reference_value(value):
    """
    One reference string -> ("url", https-url) or ("asset", asset_id).
    Accepts a bare asset ID, asset://<id> or an https:// link.
    """
    text = str(value or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith("https://"):
        return ("url", text)
    if lowered.startswith(ASSET_URI_PREFIX):
        asset_id = text[len(ASSET_URI_PREFIX):].strip()
    elif "://" in text or any(ch.isspace() for ch in text):
        raise BytePlusException(get_text("err_reference_value_invalid", value=text))
    else:
        asset_id = text
    if not asset_id:
        raise BytePlusException(get_text("err_reference_value_invalid", value=text))
    return ("asset", asset_id)


def reference_kind_from_url(url):
    """image / video / audio from the link's file extension, else None."""
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    return _URL_EXTENSION_KINDS.get(ext)


async def _probe_url_kind(url):
    """image / video / audio from the Content-Type of a HEAD request, else None."""
    timeout = aiohttp.ClientTimeout(total=_URL_PROBE_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.head(url, allow_redirects=True) as response:
                content_type = response.headers.get("Content-Type", "")
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None
    major = content_type.split("/", 1)[0].strip().lower()
    return major if major in REFERENCE_KINDS else None


def _get_asset(client, asset_id, project_name):
    from .nodes_assets import AssetLibrary

    library = AssetLibrary(client)
    return library.call("GetAsset", {"Id": asset_id, "ProjectName": project_name})


def _has_asset_credentials(client):
    from .nodes_assets import resolve_asset_credentials

    try:
        resolve_asset_credentials(client)
    except BytePlusException:
        return False
    return True


async def _lookup_asset_kind(client, asset_id, project_name):
    asset = await asyncio.to_thread(_get_asset, client, asset_id, project_name)
    status = str(asset.get("Status") or "")
    if status != "Active":
        raise BytePlusException(
            get_text("err_reference_asset_not_active", asset_id=asset_id, status=status or "unknown")
        )
    kind = _ASSET_TYPE_KINDS.get(str(asset.get("AssetType") or ""))
    if kind is None:
        raise BytePlusException(
            get_text("err_reference_asset_type_unknown", asset_id=asset_id)
        )
    return kind


async def resolve_reference_values(client, values, project_name="default"):
    """
    Reference strings (asset IDs, asset:// URIs, https links) -> list of
    {"kind": image|video|audio, "uri": str, "source": original value}, in input order.

    Asset types come from GetAsset, which needs IAM AK/SK on the API Client
    entry (or BYTEPLUS_ACCESS_KEY / BYTEPLUS_SECRET_KEY). Link types come from
    the file extension, else the Content-Type of a HEAD request.
    """
    resolved = []
    for value in values:
        parts = split_reference_value(value)
        if parts is None:
            continue
        form, target = parts
        if form == "url":
            kind = reference_kind_from_url(target) or await _probe_url_kind(target)
            if kind is None:
                raise BytePlusException(get_text("err_reference_url_type_unknown", url=target))
            resolved.append({"kind": kind, "uri": target, "source": value})
            continue
        if not _has_asset_credentials(client):
            raise BytePlusException(get_text("err_reference_asset_needs_credentials", asset_id=target))
        kind = await _lookup_asset_kind(client, target, project_name)
        resolved.append({"kind": kind, "uri": ASSET_URI_PREFIX + target, "source": value})
    return resolved


async def resolve_typed_reference(client, value, expected_kind, project_name="default"):
    """
    A reference string for a slot whose media type is fixed (e.g. first_frame_asset_id).
    Returns the URI to send, or None when the value is empty. With AK/SK the asset
    is checked to be Active and of the expected type; without them it is sent as is.
    """
    parts = split_reference_value(value)
    if parts is None:
        return None
    form, target = parts
    if form == "url":
        kind = reference_kind_from_url(target)
        if kind is not None and kind != expected_kind:
            raise BytePlusException(
                get_text("err_reference_type_mismatch", value=target, kind=kind, expected=expected_kind)
            )
        return target
    if _has_asset_credentials(client):
        kind = await _lookup_asset_kind(client, target, project_name)
        if kind != expected_kind:
            raise BytePlusException(
                get_text("err_reference_type_mismatch", value=target, kind=kind, expected=expected_kind)
            )
    return ASSET_URI_PREFIX + target
