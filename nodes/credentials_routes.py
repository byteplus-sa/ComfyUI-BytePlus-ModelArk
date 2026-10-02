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
from .nodes_shared import get_text, plain_text, validate_api_key

logger = logging.getLogger("BytePlus")

ROUTE = "/byteplus/credentials"
MAX_BODY_BYTES = 16 * 1024


def _json(payload, status=200):
    return web.json_response(payload, status=status)


def _error(message_key, status=400, **kwargs):
    return _json({"error": plain_text(message_key, **kwargs)}, status=status)


def _same_origin(request):
    """
    Reject browser requests from another site. A request without an Origin
    header (curl, scripts on the same machine) has nothing to protect against.
    """
    origin = request.headers.get("Origin")
    if not origin:
        return True
    host = request.headers.get("Host", "")
    return urlparse(origin).netloc.lower() == host.lower()


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

    if credential in ("modelark", "speech", "mediakit"):
        variable = credentials.CREDENTIAL_VARS[credential]
        if clear:
            updates[variable] = ""
        else:
            value = str(body.get("value") or "").strip()
            if not value:
                return _error("cred_empty")
            if credential == "modelark":
                region = body.get("region") or credentials.get_default_region()
                if region not in REGION_BASE_URLS:
                    return _error("cred_bad_request")
                # Same check as the API Client node; blocking, so off the event loop.
                accepted = await asyncio.to_thread(validate_api_key, value, REGION_BASE_URLS[region])
                if not accepted:
                    return _error("cred_key_rejected", 422)
                updates[credentials.REGION_ENV] = region
            updates[variable] = value
    elif credential == "iam":
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
    result["message"] = plain_text("cred_cleared" if clear else "cred_saved", path=path)
    return _json(result)


def register():
    """Add the routes to ComfyUI's server; False when there is no server (tests, tools)."""
    try:
        from server import PromptServer

        routes = PromptServer.instance.routes
    except Exception:
        return False
    routes.get(ROUTE)(handle_status)
    routes.post(ROUTE)(handle_save)
    return True
