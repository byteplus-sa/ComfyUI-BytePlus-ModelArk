"""
Private asset library (Advanced Creation Rights): virtual portraits (AIGC
groups) and verified real people (LivenessFace groups).

Assets are managed with the signed ModelArk OpenAPI (service "ark", version
2024-01-01) using IAM AK/SK, not the ModelArk API key. Active assets are used in
Seedance as asset://<asset_id> (ref_image_urls / ref_video_urls /
ref_audio_urls on the Seedance 2 / 2.5 node).
"""
import asyncio
import hashlib
import io
import json
import os
import time

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
from .nodes_shared import (
    GLOBAL_CATEGORY,
    BytePlusClientType,
    BytePlusException,
    get_text,
    log_msg,
)

GROUP_TYPES = ["AIGC", "LivenessFace"]
ASSET_STATUSES = ["all", "Active", "Processing", "Failed"]
ASSET_TYPES = ["Image", "Video", "Audio"]
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
        config = byteplussdkcore.Configuration()
        config.ak = credentials["access_key"]
        config.sk = credentials["secret_key"]
        config.session_token = credentials.get("session_token") or ""
        config.region = region
        config.host = ASSET_API_HOSTS.get(region, ASSET_API_HOSTS[DEFAULT_REGION])
        self._api = UniversalApi(byteplussdkcore.ApiClient(config))

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
            result = self._api.do_call(info, {k: v for k, v in body.items() if v is not None})
        except ApiException as e:
            raise BytePlusException(_format_asset_error(action, e))
        return result if isinstance(result, dict) else {}


def _format_asset_error(action, error):
    code, message = str(getattr(error, "status", "") or "Error"), str(error)
    try:
        payload = json.loads(getattr(error, "body", "") or "{}")
        err = (payload.get("ResponseMetadata") or {}).get("Error") or {}
        code = err.get("Code") or code
        message = err.get("Message") or message
    except (TypeError, ValueError):
        pass
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
    result = await asyncio.to_thread(
        library.call,
        "ListAssetGroups",
        {
            "Filter": {"Name": name, "GroupType": "AIGC"},
            "MaxResults": 100,
            "ProjectName": project_name,
        },
    )
    matches = [g for g in result.get("Items") or [] if g.get("Name") == name]
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
    log_msg("asset_group_created", group_id=group_id, name=name)
    return group_id


async def wait_for_asset(library, asset_id, project_name, timeout=ASSET_ACTIVE_TIMEOUT_SECONDS):
    """Poll GetAsset until Active; raise on Failed or timeout. Interruptible."""
    deadline = time.monotonic() + timeout
    last_status = None
    while True:
        comfy.model_management.throw_exception_if_processing_interrupted()
        asset = await asyncio.to_thread(
            library.call, "GetAsset", {"Id": asset_id, "ProjectName": project_name}
        )
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
        for _ in range(ASSET_POLL_SECONDS * 2):
            comfy.model_management.throw_exception_if_processing_interrupted()
            await asyncio.sleep(0.5)


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
            display_name="BytePlus Virtual Portrait Asset",
            category=GLOBAL_CATEGORY,
            description=(
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
            cache_key = (hashlib.sha256(png).hexdigest(), group_id, project_name)
            cached_id = ASSET_UPLOAD_CACHE.get(cache_key)
            if cached_id:
                asset = await asyncio.to_thread(
                    library.call, "GetAsset", {"Id": cached_id, "ProjectName": project_name}
                )
                if str(asset.get("Status")) in ("Active", "Processing"):
                    log_msg("asset_reused", asset_id=cached_id)
                    if wait_until_active:
                        asset = await wait_for_asset(library, cached_id, project_name)
                    return cls._output(asset, cached_id, group_id)
                ASSET_UPLOAD_CACHE.pop(cache_key, None)
            source_url = await upload_image_to_comfy_storage(cls, image)
            asset_type = "Image"
        else:
            source_url = image_url

        created = await asyncio.to_thread(
            library.call,
            "CreateAsset",
            {
                "GroupId": group_id,
                "URL": source_url,
                "AssetType": asset_type,
                "Name": (asset_name or "").strip() or None,
                "ProjectName": project_name,
            },
        )
        asset_id = str(created.get("Id") or "")
        if cache_key:
            ASSET_UPLOAD_CACHE[cache_key] = asset_id
        log_msg("asset_created", asset_id=asset_id)

        if wait_until_active:
            asset = await wait_for_asset(library, asset_id, project_name)
        else:
            asset = {"Id": asset_id, "GroupId": group_id, "AssetType": asset_type, "Status": "Processing"}
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
