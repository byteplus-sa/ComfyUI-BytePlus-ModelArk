"""
HTTP routes behind Settings > BytePlus: show which default credentials are set
and save or remove them in ComfyUI's user/.env.

The routes are write-only for keys: a response never contains a key, only
whether one is set, where it comes from and its last four characters. Only
requests from the page's own origin are accepted.
"""

import asyncio
import json
import logging
from urllib.parse import urlparse

from aiohttp import web

from . import credentials
from .constants import REGION_BASE_URLS
from .nodes_shared import check_api_key, get_text, plain_text

logger = logging.getLogger("BytePlus")

ROUTE = "/byteplus/credentials"
MAX_BODY_BYTES = 16 * 1024


def _json(payload, status=200):
    return web.json_response(payload, status=status)


def _error(message_key, status=400, **kwargs):
    return _json({"error": plain_text(message_key, **kwargs)}, status=status)


def _same_origin(request):
    """
    False for a request a page on another site made.

    Browsers send Sec-Fetch-Site and page scripts cannot set it, so when it is
    there it decides: "same-origin" (the Settings page) and "none" (typed in the
    address bar) pass, "same-site" and "cross-site" do not. This works behind
    reverse proxies and tunnels, where Host is the upstream address. Browsers
    without it fall back to comparing Origin with the address the browser used:
    Host, or X-Forwarded-Host behind a proxy (a page cannot add that header to
    a cross-origin request without a CORS preflight, which ComfyUI does not
    grant). A request with neither header is not from a web page (curl, local
    scripts) and passes, like ComfyUI's own origin check.
    """
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site:
        return fetch_site.lower() in ("same-origin", "none")
    origin = request.headers.get("Origin")
    if not origin:
        return True
    origin_host = urlparse(origin).netloc.lower()
    hosts = {request.headers.get("Host", "").strip().lower()}
    for host in request.headers.get("X-Forwarded-Host", "").split(","):
        hosts.add(host.strip().lower())
    hosts.discard("")
    return bool(origin_host) and origin_host in hosts


async def handle_status(request):
    if not _same_origin(request):
        return _error("cred_forbidden_origin", 403)
    return _json(credentials.credential_status())


async def handle_save(request):
    """
    Body: {"credential": "modelark" | "speech" | "mediakit" | "iam",
           "value": "<key>", "access_key": "...", "secret_key": "...",
           "region": "ap-southeast-1", "clear": true}
    ("iam" is the AK/SK pair for the asset library; "region" belongs to modelark.)
    Endpoint: {"credential": "endpoint", "endpoint": <ENDPOINT_VARS key>,
               "value": "ep-..."}; empty value or clear restores the built-in model.
    """
    if not _same_origin(request):
        return _error("cred_forbidden_origin", 403)
    try:
        # Read at most one byte more than allowed: Content-Length may be missing or wrong.
        raw = await request.content.read(MAX_BODY_BYTES + 1)
        if len(raw) > MAX_BODY_BYTES:
            return _error("cred_bad_request", 413)
        body = json.loads(raw)
    except Exception:
        return _error("cred_bad_request")
    if not isinstance(body, dict):
        return _error("cred_bad_request")

    credential = body.get("credential")
    clear = body.get("clear") is True
    updates = {}
    message_key = "cred_cleared" if clear else "cred_saved"

    if credential == "endpoint":
        endpoint = body.get("endpoint")
        if not isinstance(endpoint, str) or endpoint not in credentials.ENDPOINT_VARS:
            return _error("cred_bad_request")
        variable = credentials.ENDPOINT_VARS[endpoint]
        value = body.get("value", "")
        if not isinstance(value, str):
            return _error("cred_bad_request")
        value = "" if clear else value.strip()
        if value and not credentials.valid_endpoint_id(value):
            return _error("cred_endpoint_invalid")
        if (
            value
            and credentials.setting_source(variable) == "environment"
            and value != credentials.get_setting(variable)
        ):
            return _error("cred_endpoint_env_conflict", 409, variable=variable)
        if value and any(
            credentials.get_setting(other) == value for other in credentials.ENDPOINT_VARS.values() if other != variable
        ):
            return _error("cred_endpoint_duplicate")
        updates[variable] = value
        message_key = "cred_endpoint_saved" if value else "cred_endpoint_cleared"
    elif credential in ("modelark", "speech", "mediakit"):
        variable = credentials.CREDENTIAL_VARS[credential]
        if clear:
            updates[variable] = ""
        else:
            value = str(body.get("value") or "").strip()
            if not value:
                return _error("cred_empty")
            if credential == "modelark":
                region = body.get("region") or credentials.get_default_region()
                if not isinstance(region, str) or region not in REGION_BASE_URLS:
                    return _error("cred_bad_request")
                env_region = credentials.get_default_region()
                if credentials.setting_source(credentials.REGION_ENV) == "environment" and region != env_region:
                    # The key would be checked against one region and used against another.
                    return _error("cred_region_env_conflict", 409, region=env_region)
                # A 401 from the region endpoint; blocking, so off the event loop.
                accepted = await asyncio.to_thread(check_api_key, value, REGION_BASE_URLS[region])
                if accepted is False:
                    return _error("cred_key_rejected", 422)
                if accepted is None:
                    # Unreachable is not rejected: save, and say the key is unchecked.
                    message_key = "cred_key_unchecked"
                updates[credentials.REGION_ENV] = region
            updates[variable] = value
    elif credential == "iam":
        # A session token belongs to the old pair (STS), so it goes with it.
        updates[credentials.SESSION_TOKEN_ENV] = ""
        if clear:
            updates[credentials.CREDENTIAL_VARS["access_key"]] = ""
            updates[credentials.CREDENTIAL_VARS["secret_key"]] = ""
        else:
            access_key = str(body.get("access_key") or "").strip()
            secret_key = str(body.get("secret_key") or "").strip()
            if not access_key or not secret_key:
                return _error("cred_pair_incomplete")
            updates[credentials.CREDENTIAL_VARS["access_key"]] = access_key
            updates[credentials.CREDENTIAL_VARS["secret_key"]] = secret_key
    else:
        return _error("cred_unknown")

    try:
        path = await asyncio.to_thread(credentials.update_env_file, updates)
    except ValueError:  # e.g. a value with a line break
        return _error("cred_bad_request")
    except OSError as e:
        logger.error(get_text("cred_write_failed", path=credentials.env_file_path(), e=type(e).__name__))
        return _error("cred_write_failed", 500, path=credentials.env_file_path(), e=type(e).__name__)

    logger.info(get_text("cred_saved_log", path=path, names=", ".join(sorted(updates))))
    result = credentials.credential_status()
    result["message"] = plain_text(message_key, path=path)
    return _json(result)


def register():
    """
    Add the routes to ComfyUI's server. Without a server (tests, tools) it
    logs why and returns False, so the nodes still load.
    """
    try:
        from server import PromptServer

        routes = PromptServer.instance.routes
    except Exception as e:
        logger.warning(get_text("init_credentials_routes_failed", e=e))
        return False
    routes.get(ROUTE)(handle_status)
    routes.post(ROUTE)(handle_save)
    return True
