"""
Private asset library (Advanced Creation Rights): virtual portraits (AIGC
groups) and verified real people (LivenessFace groups).

Assets are managed with the signed ModelArk OpenAPI (service "ark", version
2024-01-01) using IAM AK/SK, not the ModelArk API key. Active assets are used in
Seedance as asset://<asset_id>: first_frame_asset_id / last_frame_asset_id and
reference_assets on the core-style Seedance 2.5 nodes (bare IDs work there),
ref_image_urls / ref_video_urls / ref_audio_urls on the legacy Seedance 2 / 2.5 node.

Nodes: BytePlusCreateImageAsset / VideoAsset / AudioAsset (shaped like core's
ByteDance Create Image / Video Asset), BytePlusAssetLibrary (ListAssets) and the
legacy BytePlusVirtualPortraitAsset.
"""
import ast
import asyncio
import hashlib
import io
import json
import os
import time
import uuid
from urllib.parse import urlparse

import comfy.model_management
import numpy
import PIL.Image
from comfy_api.latest import io as comfy_io

from .constants import (
    ASSET_ACTIVE_TIMEOUT_SECONDS,
    ASSET_API_HOSTS,
    ASSET_API_VERSION,
    ASSET_POLL_SECONDS,
    ASSET_URI_PREFIX,
    DEFAULT_REGION,
)
from .core_style import core_search_aliases, reference_kind_from_url
from .nodes_shared import (
    GLOBAL_CATEGORY,
    LOG_PREFIX,
    BytePlusClientType,
    BytePlusException,
    get_text,
    log_msg,
    sleep_interruptible,
    video_source_size_bytes,
)

GROUP_TYPES = ["AIGC", "LivenessFace"]
ASSET_STATUSES = ["all", "Active", "Processing", "Failed"]
ASSET_TYPES = ["Image", "Video", "Audio"]
ASSET_MAX_STATUS_ERRORS = 5
GROUP_LIST_PAGE_SIZE = 100
AUTH_ERROR_CODES = {"InvalidAccessKey", "SignatureDoesNotMatch", "InvalidSecretKey", "InvalidAuthorization"}
DENIED_ERROR_CODES = {"AccessDenied", "Forbidden", "UnauthorizedOperation", "NoPermission"}
THROTTLE_ERROR_CODES = {"RequestLimitExceeded", "FlowLimitExceeded", "TooManyRequests", "Throttling"}

# (image sha256, group id) -> asset id, so re-running a workflow does not
# register the same portrait again. Mutated in place.
ASSET_UPLOAD_CACHE = {}


def resolve_asset_credentials(client):
    """AK/SK from the selected api_keys.json entry, else the standard env vars."""
    credentials = getattr(client, "asset_credentials", None)
    if credentials:
        return credentials
    access_key = os.environ.get("BYTEPLUS_ACCESS_KEY") or os.environ.get("BYTEPLUS_ACCESSKEY")
    secret_key = os.environ.get("BYTEPLUS_SECRET_KEY") or os.environ.get("BYTEPLUS_SECRETKEY")
    if access_key and secret_key:
        return {
            "access_key": access_key,
            "secret_key": secret_key,
            "session_token": os.environ.get("BYTEPLUS_SESSION_TOKEN", ""),
        }
    raise BytePlusException(get_text("err_asset_credentials_missing"))


class AssetLibrary:
    """Signed calls to the asset library OpenAPI through the SDK's UniversalApi."""

    def __init__(self, client):
        import byteplussdkcore
        from byteplussdkcore.universal import UniversalApi

        credentials = resolve_asset_credentials(client)
        region = getattr(client, "region", DEFAULT_REGION) or DEFAULT_REGION

        def api_client(auto_retry):
            config = byteplussdkcore.Configuration()
            config.ak = credentials["access_key"]
            config.sk = credentials["secret_key"]
            config.session_token = credentials.get("session_token") or ""
            config.region = region
            config.host = ASSET_API_HOSTS.get(region, ASSET_API_HOSTS[DEFAULT_REGION])
            config.auto_retry = auto_retry
            return UniversalApi(byteplussdkcore.ApiClient(config))

        # The SDK retries timeouts, 429 and 5xx. Lookups keep that; Create* actions
        # do not: a retried timeout could create a second asset or group (the
        # actions take no idempotency token).
        self._api = api_client(True)
        self._create_api = api_client(False)
        self.region = region
        # Identifies the account in cache keys without keeping the key itself.
        self.account_fingerprint = hashlib.sha256(credentials["access_key"].encode()).hexdigest()[:16]

    def call(self, action, body):
        """Blocking call; returns the Result object. Run it with asyncio.to_thread."""
        from byteplussdkcore.rest import ApiException
        from byteplussdkcore.universal import UniversalInfo

        info = UniversalInfo(
            method="POST",
            service="ark",
            version=ASSET_API_VERSION,
            action=action,
            content_type="application/json",
        )
        try:
            api = self._create_api if action.startswith("Create") else self._api
            result = api.do_call(info, {k: v for k, v in body.items() if v is not None})
        except ApiException as e:
            raise BytePlusException(_format_asset_error(action, e))
        return result if isinstance(result, dict) else {}


def _parse_error_payload(error):
    """
    The provider error as a dict. Non-2xx responses carry the JSON body; the
    SDK raises HTTP-200 business errors with the error dict as `reason`.
    """
    body = getattr(error, "body", None)
    if body:
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            err = (payload.get("ResponseMetadata") or {}).get("Error")
            if isinstance(err, dict):
                return err
    reason = getattr(error, "reason", None)
    if isinstance(reason, dict):
        return reason
    if isinstance(reason, str) and reason.strip().startswith("{"):
        try:
            parsed = ast.literal_eval(reason.strip())
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return {}


def _format_asset_error(action, error):
    err = _parse_error_payload(error)
    code = str(err.get("Code") or getattr(error, "status", "") or "Error")
    message = str(err.get("Message") or getattr(error, "reason", "") or error)
    hint = ""
    if code in AUTH_ERROR_CODES:
        hint = get_text("hint_asset_auth")
    elif code in DENIED_ERROR_CODES:
        hint = get_text("hint_asset_denied")
    elif code in THROTTLE_ERROR_CODES:
        hint = get_text("hint_asset_throttled")
    return get_text("err_asset_api", action=action, code=code, message=message, hint=hint)


def _tensor_to_png_bytes(image):
    rgb = numpy.clip(image[0].cpu().numpy() * 255.0, 0, 255).astype(numpy.uint8)
    buffer = io.BytesIO()
    PIL.Image.fromarray(rgb, "RGB").save(buffer, format="PNG")
    return buffer.getvalue()


async def upload_image_to_comfy_storage(node_cls, image):
    """
    Upload an image to Comfy.org storage and return its HTTPS URL (CreateAsset
    only accepts URLs). Needs a Comfy.org login; the helper is internal to
    ComfyUI and missing with --disable-api-nodes.
    """
    try:
        from comfy_api_nodes.util import upload_image_to_comfyapi
    except Exception as e:
        raise BytePlusException(get_text("err_comfy_image_upload_unavailable", e=e))
    try:
        return await upload_image_to_comfyapi(
            node_cls, image[:1], mime_type="image/png", wait_label=None, total_pixels=None
        )
    except comfy.model_management.InterruptProcessingException:
        raise
    except Exception as e:
        raise BytePlusException(get_text("err_comfy_image_upload_failed", e=e))


async def find_or_create_group(library, name, project_name):
    """Reuse the single AIGC group with exactly this name, or create it."""
    matches, next_token = [], None
    while True:
        result = await asyncio.to_thread(
            library.call,
            "ListAssetGroups",
            {
                "Filter": {"Name": name, "GroupType": "AIGC"},
                "MaxResults": GROUP_LIST_PAGE_SIZE,
                "NextToken": next_token,
                "ProjectName": project_name,
            },
        )
        matches += [g for g in result.get("Items") or [] if g.get("Name") == name]
        next_token = result.get("NextToken")
        if not next_token:
            break
    if len(matches) > 1:
        raise BytePlusException(get_text("err_asset_group_ambiguous", count=len(matches), name=name))
    if matches:
        return str(matches[0].get("Id"))
    created = await asyncio.to_thread(
        library.call,
        "CreateAssetGroup",
        {
            "Name": name,
            "Description": "Created by ComfyUI BytePlus ModelArk",
            "GroupType": "AIGC",
            "ProjectName": project_name,
        },
    )
    group_id = str(created.get("Id") or "")
    if not group_id:
        raise BytePlusException(get_text("err_asset_no_id", action="CreateAssetGroup"))
    log_msg("asset_group_created", group_id=group_id, name=name)
    return group_id


async def wait_for_asset(library, asset_id, project_name, timeout=ASSET_ACTIVE_TIMEOUT_SECONDS):
    """Poll GetAsset until Active; raise on Failed or timeout. Interruptible."""
    deadline = time.monotonic() + timeout
    last_status = None
    status_errors = 0
    while True:
        comfy.model_management.throw_exception_if_processing_interrupted()
        try:
            asset = await asyncio.to_thread(
                library.call, "GetAsset", {"Id": asset_id, "ProjectName": project_name}
            )
        except BytePlusException:
            # Throttling or a network error: keep waiting, the asset is still processing.
            status_errors += 1
            if status_errors >= ASSET_MAX_STATUS_ERRORS or time.monotonic() >= deadline:
                raise
            await sleep_interruptible(ASSET_POLL_SECONDS)
            continue
        status_errors = 0
        status = str(asset.get("Status") or "")
        if status != last_status:
            log_msg("asset_status", asset_id=asset_id, status=status or "unknown")
            last_status = status
        if status == "Active":
            return asset
        if status == "Failed":
            raise BytePlusException(get_text("err_asset_failed", asset_id=asset_id, status=status))
        if time.monotonic() >= deadline:
            raise BytePlusException(
                get_text("err_asset_timeout", asset_id=asset_id, status=status, seconds=int(timeout))
            )
        await sleep_interruptible(ASSET_POLL_SECONDS)


def _asset_cache_key(digest, library, project_name, group_id, asset_name):
    """Dedupe key for ASSET_UPLOAD_CACHE: same media, account, region, project, group and name."""
    return (
        digest,
        library.account_fingerprint,
        library.region,
        project_name,
        group_id,
        (asset_name or "").strip(),
    )


async def reuse_cached_asset(library, cache_key, project_name, wait_until_active):
    """(asset, asset_id) of an asset created earlier for the same media that still exists, else None."""
    cached_id = ASSET_UPLOAD_CACHE.get(cache_key)
    if not cached_id:
        return None
    try:
        asset = await asyncio.to_thread(
            library.call, "GetAsset", {"Id": cached_id, "ProjectName": project_name}
        )
    except BytePlusException:
        # Deleted in the console, or not visible to these credentials: create it again.
        asset = {}
    if str(asset.get("Status")) in ("Active", "Processing"):
        log_msg("asset_reused", asset_id=cached_id)
        if wait_until_active:
            asset = await wait_for_asset(library, cached_id, project_name)
        return asset, cached_id
    ASSET_UPLOAD_CACHE.pop(cache_key, None)
    return None


async def create_asset(
    library, *, group_id, url, asset_type, asset_name, project_name, wait_until_active, cache_key=None
):
    """CreateAsset from an HTTPS URL, remember it for dedupe, optionally wait until Active."""
    created = await asyncio.to_thread(
        library.call,
        "CreateAsset",
        {
            "GroupId": group_id,
            "URL": url,
            "AssetType": asset_type,
            "Name": (asset_name or "").strip() or None,
            "ProjectName": project_name,
        },
    )
    asset_id = str(created.get("Id") or "")
    if not asset_id:
        raise BytePlusException(get_text("err_asset_no_id", action="CreateAsset"))
    if cache_key:
        ASSET_UPLOAD_CACHE[cache_key] = asset_id
    log_msg("asset_created", asset_id=asset_id)

    if wait_until_active:
        asset = await wait_for_asset(library, asset_id, project_name)
    else:
        asset = {"Id": asset_id, "GroupId": group_id, "AssetType": asset_type, "Status": "Processing"}
    return asset, asset_id


def _asset_summary(asset, asset_id=None):
    asset_id = str(asset.get("Id") or asset_id or "")
    return {
        "asset_id": asset_id,
        "asset_uri": ASSET_URI_PREFIX + asset_id,
        "name": asset.get("Name"),
        "group_id": asset.get("GroupId"),
        "asset_type": asset.get("AssetType"),
        "status": asset.get("Status"),
        "create_time": asset.get("CreateTime"),
    }


class BytePlusVirtualPortraitAsset(comfy_io.ComfyNode):
    """
    Register an authorized portrait (or other media) in the private asset
    library and wait until it is Active; outputs its asset:// URI for Seedance.
    """

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusVirtualPortraitAsset",
            display_name="BytePlus Virtual Portrait Asset (Legacy)",
            category=GLOBAL_CATEGORY,
            is_deprecated=True,
            description=(
                "Legacy node: use BytePlus Create Image / Video / Audio Asset instead. "
                "Add an authorized portrait to your private asset library (Dreamina Seedance "
                "Advanced Creation Rights) and output its asset:// URI for the Seedance 2 / 2.5 "
                "ref_image_urls input. Needs IAM AK/SK (see README). A connected image is "
                "uploaded to Comfy.org storage first to get the HTTPS URL CreateAsset needs."
            ),
            is_output_node=True,
            inputs=[
                BytePlusClientType.Input("client"),
                comfy_io.Image.Input("image", optional=True),
                comfy_io.String.Input(
                    "image_url",
                    default="",
                    optional=True,
                    tooltip="Public HTTPS URL of the media (used when no image is connected).",
                ),
                comfy_io.Combo.Input(
                    "asset_type",
                    options=ASSET_TYPES,
                    default="Image",
                    tooltip="Type of the media at image_url. A connected image is always Image.",
                ),
                comfy_io.String.Input(
                    "group_name",
                    default="ComfyUI Virtual Portraits",
                    tooltip="Virtual portrait (AIGC) group to use, matched by exact name; created if missing. Use one group per person.",
                ),
                comfy_io.String.Input(
                    "group_id",
                    default="",
                    tooltip="Existing group ID (AIGC, or a verified person's LivenessFace group). Overrides group_name.",
                ),
                comfy_io.String.Input("asset_name", default="ComfyUI portrait"),
                comfy_io.String.Input("project_name", default="default"),
                comfy_io.Boolean.Input(
                    "wait_until_active",
                    default=True,
                    tooltip="Wait (up to 10 min) until the asset can be used in Seedance.",
                ),
            ],
            hidden=[
                comfy_io.Hidden.auth_token_comfy_org,
                comfy_io.Hidden.api_key_comfy_org,
                comfy_io.Hidden.unique_id,
            ],
            outputs=[
                comfy_io.String.Output(display_name="asset_uri"),
                comfy_io.String.Output(display_name="asset_id"),
                comfy_io.String.Output(display_name="group_id"),
                comfy_io.String.Output(display_name="info"),
            ],
        )

    @classmethod
    async def execute(
        cls,
        client,
        asset_type="Image",
        group_name="ComfyUI Virtual Portraits",
        group_id="",
        asset_name="ComfyUI portrait",
        project_name="default",
        wait_until_active=True,
        image=None,
        image_url="",
    ) -> comfy_io.NodeOutput:
        image_url = (image_url or "").strip()
        if image is None and not image_url:
            raise BytePlusException(get_text("err_asset_source_missing"))
        if image is None and not image_url.lower().startswith("https://"):
            raise BytePlusException(get_text("err_asset_url_invalid", url=image_url))

        library = AssetLibrary(client)
        project_name = (project_name or "").strip() or "default"
        group_id = (group_id or "").strip()
        if not group_id:
            group_id = await find_or_create_group(
                library, (group_name or "").strip() or "ComfyUI Virtual Portraits", project_name
            )

        cache_key = None
        if image is not None:
            png = await asyncio.to_thread(_tensor_to_png_bytes, image)
            cache_key = _asset_cache_key(
                hashlib.sha256(png).hexdigest(), library, project_name, group_id, asset_name
            )
            reused = await reuse_cached_asset(library, cache_key, project_name, wait_until_active)
            if reused:
                return cls._output(reused[0], reused[1], group_id)
            source_url = await upload_image_to_comfy_storage(cls, image)
            asset_type = "Image"
        else:
            source_url = image_url

        asset, asset_id = await create_asset(
            library,
            group_id=group_id,
            url=source_url,
            asset_type=asset_type,
            asset_name=asset_name,
            project_name=project_name,
            wait_until_active=wait_until_active,
            cache_key=cache_key,
        )
        return cls._output(asset, asset_id, group_id)

    @staticmethod
    def _output(asset, asset_id, group_id):
        summary = _asset_summary(asset, asset_id)
        summary["group_id"] = summary["group_id"] or group_id
        return comfy_io.NodeOutput(
            summary["asset_uri"],
            summary["asset_id"],
            summary["group_id"],
            json.dumps(summary, indent=2, ensure_ascii=False),
        )


class BytePlusAssetLibrary(comfy_io.ComfyNode):
    """List assets in the private asset library."""

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusAssetLibrary",
            display_name="BytePlus Asset Library",
            category=GLOBAL_CATEGORY,
            description=(
                "List assets in your private asset library (virtual portraits or verified real "
                "people). asset_uris (one per line) can go straight into the Seedance 2 / 2.5 "
                "ref_image_urls, ref_video_urls or ref_audio_urls inputs."
            ),
            is_output_node=True,
            inputs=[
                BytePlusClientType.Input("client"),
                comfy_io.Combo.Input("group_type", options=GROUP_TYPES, default="AIGC"),
                comfy_io.String.Input("group_id", default="", tooltip="Only assets in this group (optional)."),
                comfy_io.Combo.Input("status", options=ASSET_STATUSES, default="Active"),
                comfy_io.String.Input("name", default="", tooltip="Filter by asset name (optional)."),
                comfy_io.Int.Input("max_results", default=20, min=1, max=100),
                comfy_io.String.Input("project_name", default="default"),
            ],
            outputs=[
                comfy_io.String.Output(display_name="asset_uris"),
                comfy_io.String.Output(display_name="assets_json"),
            ],
        )

    @classmethod
    async def execute(
        cls,
        client,
        group_type="AIGC",
        group_id="",
        status="Active",
        name="",
        max_results=20,
        project_name="default",
    ) -> comfy_io.NodeOutput:
        library = AssetLibrary(client)
        group_id = (group_id or "").strip()
        result = await asyncio.to_thread(
            library.call,
            "ListAssets",
            {
                "Filter": {
                    k: v
                    for k, v in {
                        "GroupType": group_type,
                        "GroupIds": [group_id] if group_id else None,
                        "Statuses": [status] if status != "all" else None,
                        "Name": (name or "").strip() or None,
                    }.items()
                    if v is not None
                },
                "MaxResults": int(max_results),
                "ProjectName": (project_name or "").strip() or "default",
            },
        )
        assets = [_asset_summary(item) for item in result.get("Items") or [] if isinstance(item, dict)]
        return comfy_io.NodeOutput(
            "\n".join(a["asset_uri"] for a in assets),
            json.dumps(assets, indent=2, ensure_ascii=False),
        )


# --------------------------------------------------------------------------
# Nodes shaped like ComfyUI core's ByteDance Create Image / Video Asset nodes
# (plus an Audio variant). Registered in __init__.py.
# --------------------------------------------------------------------------

DEFAULT_GROUP_NAME = "ComfyUI Virtual Portraits"
# BytePlus CreateAsset limits (Create Asset API / real-person asset guide).
# Core's own checks are stricter for video (2-15 s, 409,600-927,408 px).
ASSET_IMAGE_MIN_EDGE = 300
ASSET_IMAGE_MAX_EDGE = 6000
ASSET_MIN_RATIO = 0.4
ASSET_MAX_RATIO = 2.5
ASSET_VIDEO_MIN_SECONDS = 2.0
ASSET_VIDEO_MAX_SECONDS = 30.0
ASSET_VIDEO_MIN_PIXELS = 407_696
ASSET_VIDEO_MAX_PIXELS = 8_295_044
ASSET_VIDEO_MAX_BYTES = 200 * 1024 * 1024
ASSET_VIDEO_MIN_FPS = 24.0
ASSET_VIDEO_MAX_FPS = 60.0
# BytePlus audio asset limits: wav/mp3, 2-30 s, up to 15 MB.
ASSET_AUDIO_MIN_SECONDS = 2.0
ASSET_AUDIO_MAX_SECONDS = 30.0
ASSET_AUDIO_MAX_BYTES = 15 * 1024 * 1024
ASSET_AUDIO_URL_EXTENSIONS = ("", ".wav", ".mp3")

GROUP_ID_TOOLTIP = (
    "Reuse an existing asset group ID to add more assets of the same person: a virtual portrait "
    "(AIGC) group, or a verified real person's LivenessFace group. Leave empty to find or create "
    "the virtual portrait (AIGC) group named group_name. Real-person verification is a QR-code flow "
    "in the ModelArk console (BytePlus has no API for it), so unlike ComfyUI core's node this node "
    "does not start it: verify the person in the console, then paste the group ID here."
)


def _core_asset_inputs(media_input, url_input, url_tooltip, asset_name_default):
    """client first, then core's inputs, then this pack's extras (advanced)."""
    return [
        BytePlusClientType.Input("client"),
        media_input,
        comfy_io.String.Input("group_id", default="", tooltip=GROUP_ID_TOOLTIP),
        comfy_io.String.Input(url_input, default="", tooltip=url_tooltip, advanced=True),
        comfy_io.String.Input(
            "group_name",
            default=DEFAULT_GROUP_NAME,
            tooltip="Used when group_id is empty: the virtual portrait (AIGC) group, matched by exact "
            "name and created if missing. Use one group per person.",
            advanced=True,
        ),
        comfy_io.String.Input(
            "asset_name", default=asset_name_default, tooltip="Name of the new asset.", advanced=True
        ),
        comfy_io.String.Input(
            "project_name",
            default="default",
            tooltip="ModelArk project that holds the asset library.",
            advanced=True,
        ),
        comfy_io.Boolean.Input(
            "wait_until_active",
            default=True,
            tooltip="Wait (up to 10 min) until the asset is Active and can be used in Seedance.",
            advanced=True,
        ),
    ]


def _core_asset_outputs():
    return [
        comfy_io.String.Output("asset_id", display_name="asset_id"),
        comfy_io.String.Output("group_id", display_name="group_id"),
        comfy_io.String.Output(
            "asset_uri", display_name="asset_uri", tooltip="asset://<asset_id>, for inputs that take URIs."
        ),
        comfy_io.String.Output("info", display_name="info", tooltip="The asset as JSON."),
    ]


def _core_asset_hidden():
    return [
        comfy_io.Hidden.auth_token_comfy_org,
        comfy_io.Hidden.api_key_comfy_org,
        comfy_io.Hidden.unique_id,
    ]


def check_asset_source(media, url, media_name, url_input, expected_kind):
    """Exactly one of the media socket and the URL; the URL must be https and not another media type."""
    url = str(url or "").strip()
    if media is None and not url:
        raise BytePlusException(get_text("err_asset_media_missing", media=media_name, url_input=url_input))
    if media is not None and url:
        raise BytePlusException(get_text("err_asset_media_and_url", media=media_name, url_input=url_input))
    if url:
        if not url.lower().startswith("https://"):
            raise BytePlusException(get_text("err_asset_url_not_https", url_input=url_input, url=url))
        kind = reference_kind_from_url(url)
        if kind is not None and kind != expected_kind:
            raise BytePlusException(
                get_text("err_reference_type_mismatch", value=url, kind=kind, expected=expected_kind)
            )
    return url


def validate_asset_image(image):
    width, height = int(image.shape[-2]), int(image.shape[-3])
    if not (
        ASSET_IMAGE_MIN_EDGE <= width <= ASSET_IMAGE_MAX_EDGE
        and ASSET_IMAGE_MIN_EDGE <= height <= ASSET_IMAGE_MAX_EDGE
    ):
        raise BytePlusException(
            get_text(
                "err_asset_image_size",
                min=ASSET_IMAGE_MIN_EDGE,
                max=ASSET_IMAGE_MAX_EDGE,
                width=width,
                height=height,
            )
        )
    ratio = width / height
    if not (ASSET_MIN_RATIO < ratio < ASSET_MAX_RATIO):
        raise BytePlusException(
            get_text("err_asset_image_ratio", min=ASSET_MIN_RATIO, max=ASSET_MAX_RATIO, ratio=f"{ratio:.3f}")
        )


def validate_asset_video(video):
    duration = float(video.get_duration())
    if not (ASSET_VIDEO_MIN_SECONDS <= duration <= ASSET_VIDEO_MAX_SECONDS):
        raise BytePlusException(
            get_text(
                "err_asset_video_duration",
                min=int(ASSET_VIDEO_MIN_SECONDS),
                max=int(ASSET_VIDEO_MAX_SECONDS),
                duration=f"{duration:.2f}",
            )
        )
    width, height = video.get_dimensions()
    if not (
        ASSET_IMAGE_MIN_EDGE <= width <= ASSET_IMAGE_MAX_EDGE
        and ASSET_IMAGE_MIN_EDGE <= height <= ASSET_IMAGE_MAX_EDGE
    ):
        raise BytePlusException(
            get_text(
                "err_asset_video_size",
                min=ASSET_IMAGE_MIN_EDGE,
                max=ASSET_IMAGE_MAX_EDGE,
                width=width,
                height=height,
            )
        )
    ratio = width / height
    if not (ASSET_MIN_RATIO <= ratio <= ASSET_MAX_RATIO):
        raise BytePlusException(
            get_text(
                "err_asset_video_ratio",
                min=ASSET_MIN_RATIO,
                max=ASSET_MAX_RATIO,
                ratio=f"{ratio:.3f}",
                width=width,
                height=height,
            )
        )
    pixels = width * height
    if not (ASSET_VIDEO_MIN_PIXELS <= pixels <= ASSET_VIDEO_MAX_PIXELS):
        raise BytePlusException(
            get_text(
                "err_asset_video_pixels",
                min=f"{ASSET_VIDEO_MIN_PIXELS:,}",
                max=f"{ASSET_VIDEO_MAX_PIXELS:,}",
                pixels=f"{pixels:,}",
                width=width,
                height=height,
            )
        )
    size_bytes = video_source_size_bytes(video)
    if size_bytes is not None and size_bytes > ASSET_VIDEO_MAX_BYTES:
        raise BytePlusException(
            get_text(
                "err_asset_video_too_large",
                max_mb=ASSET_VIDEO_MAX_BYTES // (1024 * 1024),
                size_mb=f"{size_bytes / (1024.0 * 1024.0):.1f}",
            )
        )
    fps = float(video.get_frame_rate())
    if not (ASSET_VIDEO_MIN_FPS <= fps <= ASSET_VIDEO_MAX_FPS):
        raise BytePlusException(
            get_text(
                "err_asset_video_fps",
                min=int(ASSET_VIDEO_MIN_FPS),
                max=int(ASSET_VIDEO_MAX_FPS),
                fps=f"{fps:.2f}",
            )
        )


def prepare_asset_audio(audio):
    """Validate a connected AUDIO against the audio asset limits; returns its WAV bytes."""
    from .audio_utils import audio_duration, audio_to_wav_bytes

    duration = audio_duration(audio)
    if not (ASSET_AUDIO_MIN_SECONDS <= duration <= ASSET_AUDIO_MAX_SECONDS):
        raise BytePlusException(
            get_text(
                "err_asset_audio_duration",
                min=int(ASSET_AUDIO_MIN_SECONDS),
                max=int(ASSET_AUDIO_MAX_SECONDS),
                duration=f"{duration:.2f}",
            )
        )
    wav = audio_to_wav_bytes(audio)
    if len(wav) > ASSET_AUDIO_MAX_BYTES:
        raise BytePlusException(
            get_text(
                "err_asset_audio_size",
                max_mb=ASSET_AUDIO_MAX_BYTES // (1024 * 1024),
                size_mb=f"{len(wav) / (1024.0 * 1024.0):.2f}",
            )
        )
    return wav


def check_asset_audio_url(url):
    extension = os.path.splitext(urlparse(url).path)[1].lower()
    if extension not in ASSET_AUDIO_URL_EXTENSIONS:
        raise BytePlusException(get_text("err_asset_audio_format", url=url))


async def upload_asset_video(node_cls, video):
    """Comfy.org upload of a connected video (cached like reference videos)."""
    from .nodes_video import upload_videos_to_comfy_storage_cached

    urls = await upload_videos_to_comfy_storage_cached(
        node_cls,
        [video],
        unavailable_key="err_comfy_video_upload_unavailable_asset",
        failed_key="err_comfy_video_upload_failed_asset",
    )
    return urls[0]


async def upload_asset_audio(node_cls, wav_bytes):
    """Comfy.org upload of connected audio as WAV (CreateAsset needs an HTTPS URL)."""
    try:
        from comfy_api_nodes.util import upload_file_to_comfyapi
    except Exception as e:
        raise BytePlusException(get_text("err_comfy_audio_upload_unavailable_asset", e=e))
    try:
        return await upload_file_to_comfyapi(
            node_cls, io.BytesIO(wav_bytes), f"{uuid.uuid4()}.wav", "audio/wav", wait_label=None
        )
    except comfy.model_management.InterruptProcessingException:
        raise
    except Exception as e:
        raise BytePlusException(get_text("err_comfy_audio_upload_failed_asset", e=e))


def _video_digest(video):
    from .nodes_video import BytePlusVideoBase

    key = BytePlusVideoBase()._build_comfy_video_upload_cache_key(video)
    return f"video:{key}" if key else None


def _send_progress_text(cls, text):
    try:
        from server import PromptServer

        PromptServer.instance.send_progress_text(text.replace(LOG_PREFIX, "", 1), cls.hidden.unique_id)
    except Exception:
        pass


def _core_asset_output(cls, asset, asset_id, group_id):
    summary = _asset_summary(asset, asset_id)
    summary["group_id"] = summary["group_id"] or group_id
    _send_progress_text(
        cls, get_text("asset_ids_saved_hint", asset_id=summary["asset_id"], group_id=summary["group_id"])
    )
    return comfy_io.NodeOutput(
        summary["asset_id"],
        summary["group_id"],
        summary["asset_uri"],
        json.dumps(summary, indent=2, ensure_ascii=False),
    )


async def register_core_asset(
    cls,
    client,
    *,
    asset_type,
    source_url,
    digest,
    upload,
    group_id,
    group_name,
    asset_name,
    project_name,
    wait_until_active,
):
    """
    Shared flow of the Create Image / Video / Audio Asset nodes: resolve the
    group (existing group_id, else find or create the AIGC group by name),
    reuse an asset created for the same media, upload connected media to
    Comfy.org, CreateAsset, optionally wait until Active.
    """
    library = AssetLibrary(client)
    project_name = (project_name or "").strip() or "default"
    group_id = (group_id or "").strip()
    if not group_id:
        group_id = await find_or_create_group(
            library, (group_name or "").strip() or DEFAULT_GROUP_NAME, project_name
        )
    cache_key = None
    if upload is not None:
        if digest:
            cache_key = _asset_cache_key(digest, library, project_name, group_id, asset_name)
            reused = await reuse_cached_asset(library, cache_key, project_name, wait_until_active)
            if reused:
                return _core_asset_output(cls, reused[0], reused[1], group_id)
        source_url = await upload()
    asset, asset_id = await create_asset(
        library,
        group_id=group_id,
        url=source_url,
        asset_type=asset_type,
        asset_name=asset_name,
        project_name=project_name,
        wait_until_active=wait_until_active,
        cache_key=cache_key,
    )
    return _core_asset_output(cls, asset, asset_id, group_id)


_ASSET_NODE_NOTE = (
    " Needs IAM AK/SK with asset-library permission (see README). CreateAsset takes an HTTPS URL, so a "
    "connected {media} is uploaded to Comfy.org storage first (needs a Comfy.org login); or pass a public "
    "{url_input}. With an empty group_id the virtual portrait (AIGC) group named group_name is found or "
    "created."
)


class BytePlusCreateImageAsset(comfy_io.ComfyNode):
    """Core's ByteDanceCreateImageAsset on the BytePlus asset library OpenAPI."""

    NODE_ID = "BytePlusCreateImageAsset"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id=cls.NODE_ID,
            display_name="BytePlus Create Image Asset",
            search_aliases=core_search_aliases(cls.NODE_ID),
            category=GLOBAL_CATEGORY,
            description="Create a Seedance 2.0 / 2.5 personal image asset in your private asset library "
            "(Advanced Creation Rights) and output its asset_id and group_id."
            + _ASSET_NODE_NOTE.format(media="image", url_input="image_url"),
            inputs=_core_asset_inputs(
                comfy_io.Image.Input(
                    "image",
                    optional=True,
                    tooltip="Image to register as a personal asset. Leave unconnected to use image_url instead.",
                ),
                "image_url",
                "Public HTTPS URL of the image, used instead of the image input: sent straight to "
                "CreateAsset without a Comfy.org upload.",
                "ComfyUI portrait",
            ),
            outputs=_core_asset_outputs(),
            hidden=_core_asset_hidden(),
        )

    @classmethod
    async def execute(
        cls,
        client,
        group_id="",
        image_url="",
        group_name=DEFAULT_GROUP_NAME,
        asset_name="ComfyUI portrait",
        project_name="default",
        wait_until_active=True,
        image=None,
    ) -> comfy_io.NodeOutput:
        url = check_asset_source(image, image_url, "image", "image_url", "image")
        digest = upload = None
        if image is not None:
            validate_asset_image(image)
            png = await asyncio.to_thread(_tensor_to_png_bytes, image)
            digest = hashlib.sha256(png).hexdigest()

            async def upload():
                return await upload_image_to_comfy_storage(cls, image)

        return await register_core_asset(
            cls,
            client,
            asset_type="Image",
            source_url=url,
            digest=digest,
            upload=upload,
            group_id=group_id,
            group_name=group_name,
            asset_name=asset_name,
            project_name=project_name,
            wait_until_active=wait_until_active,
        )


class BytePlusCreateVideoAsset(comfy_io.ComfyNode):
    """Core's ByteDanceCreateVideoAsset on the BytePlus asset library OpenAPI."""

    NODE_ID = "BytePlusCreateVideoAsset"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id=cls.NODE_ID,
            display_name="BytePlus Create Video Asset",
            search_aliases=core_search_aliases(cls.NODE_ID),
            category=GLOBAL_CATEGORY,
            description="Create a Seedance 2.0 / 2.5 personal video asset in your private asset library "
            "(Advanced Creation Rights) and output its asset_id and group_id."
            + _ASSET_NODE_NOTE.format(media="video", url_input="video_url"),
            inputs=_core_asset_inputs(
                comfy_io.Video.Input(
                    "video",
                    optional=True,
                    tooltip="Video to register as a personal asset. Leave unconnected to use video_url instead.",
                ),
                "video_url",
                "Public HTTPS URL of the video (mp4/mov), used instead of the video input: sent straight "
                "to CreateAsset without a Comfy.org upload.",
                "ComfyUI video",
            ),
            outputs=_core_asset_outputs(),
            hidden=_core_asset_hidden(),
        )

    @classmethod
    async def execute(
        cls,
        client,
        group_id="",
        video_url="",
        group_name=DEFAULT_GROUP_NAME,
        asset_name="ComfyUI video",
        project_name="default",
        wait_until_active=True,
        video=None,
    ) -> comfy_io.NodeOutput:
        url = check_asset_source(video, video_url, "video", "video_url", "video")
        digest = upload = None
        if video is not None:
            validate_asset_video(video)
            digest = await asyncio.to_thread(_video_digest, video)

            async def upload():
                return await upload_asset_video(cls, video)

        return await register_core_asset(
            cls,
            client,
            asset_type="Video",
            source_url=url,
            digest=digest,
            upload=upload,
            group_id=group_id,
            group_name=group_name,
            asset_name=asset_name,
            project_name=project_name,
            wait_until_active=wait_until_active,
        )


class BytePlusCreateAudioAsset(comfy_io.ComfyNode):
    """Audio counterpart of the Create Image / Video Asset nodes (core has none)."""

    NODE_ID = "BytePlusCreateAudioAsset"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id=cls.NODE_ID,
            display_name="BytePlus Create Audio Asset",
            category=GLOBAL_CATEGORY,
            description="Create a Seedance 2.0 / 2.5 personal audio asset (wav/mp3, 2-30 s, up to 15 MB) in "
            "your private asset library (Advanced Creation Rights) and output its asset_id and group_id."
            + _ASSET_NODE_NOTE.format(media="audio clip (as WAV)", url_input="audio_url"),
            inputs=_core_asset_inputs(
                comfy_io.Audio.Input(
                    "audio",
                    optional=True,
                    tooltip="Audio to register as a personal asset (2-30 s). Leave unconnected to use "
                    "audio_url instead.",
                ),
                "audio_url",
                "Public HTTPS URL of a .wav or .mp3 file, used instead of the audio input: sent straight "
                "to CreateAsset without a Comfy.org upload.",
                "ComfyUI audio",
            ),
            outputs=_core_asset_outputs(),
            hidden=_core_asset_hidden(),
        )

    @classmethod
    async def execute(
        cls,
        client,
        group_id="",
        audio_url="",
        group_name=DEFAULT_GROUP_NAME,
        asset_name="ComfyUI audio",
        project_name="default",
        wait_until_active=True,
        audio=None,
    ) -> comfy_io.NodeOutput:
        url = check_asset_source(audio, audio_url, "audio", "audio_url", "audio")
        digest = upload = None
        if url:
            check_asset_audio_url(url)
        else:
            wav = await asyncio.to_thread(prepare_asset_audio, audio)
            digest = hashlib.sha256(wav).hexdigest()

            async def upload():
                return await upload_asset_audio(cls, wav)

        return await register_core_asset(
            cls,
            client,
            asset_type="Audio",
            source_url=url,
            digest=digest,
            upload=upload,
            group_id=group_id,
            group_name=group_name,
            asset_name=asset_name,
            project_name=project_name,
            wait_until_active=wait_until_active,
        )


CORE_STYLE_NODES = [BytePlusCreateImageAsset, BytePlusCreateVideoAsset, BytePlusCreateAudioAsset]
