import asyncio
import ipaddress
import json
import re
from urllib.parse import urlsplit

import aiohttp

from . import credentials
from .constants import OMNIHUMAN_API_KEY_ENV, OMNIHUMAN_BASE_URL, OMNIHUMAN_REQ_KEY
from .http_requests import RetryableRequestError, retry_transient, send_http_request
from .nodes_shared import BytePlusException, get_text, send_node_text, sleep_interruptible, wait_interruptible

REQ_KEY = OMNIHUMAN_REQ_KEY
SUBMIT_PATH = "/api/common/v3/submit_task_v2"
RESULT_PATH = "/api/common/v3/get_result_v2"
CANCEL_PATH = "/api/common/v3/cancel_task"
POLL_SECONDS = 5
MAX_POLL_ERRORS = 5
REQUEST_TIMEOUT = 60
TASK_STATES = frozenset(("in_queue", "generating", "done", "canceled", "expired", "not_found"))
_URL_PATTERN = re.compile(r"(?:https?|tos)://[^\s<>\"']+", re.IGNORECASE)
_SECRET_PATTERN = re.compile(r"key|secret|authorization|password|token|signature|credential", re.IGNORECASE)


def invalid(detail):
    raise BytePlusException(get_text("err_omnihuman_input", detail=detail))


def validate_url(value):
    if (
        not isinstance(value, str)
        or not value
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        invalid("Media URLs must be public HTTP(S) links.")
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        invalid("Media URL is malformed.")
    if parsed.scheme not in ("http", "https") or not host or parsed.username is not None or parsed.password is not None:
        invalid("Media URLs must be public HTTP(S) links without embedded credentials.")
    if port is not None and not 1 <= port <= 65535:
        invalid("Media URL port is invalid.")
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local")):
        invalid("Media URL host must be public.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        invalid("Media URL address must be public.")
    return value


def validate_task_id(task_id):
    if (
        not isinstance(task_id, str)
        or not 0 < len(task_id) <= 512
        or not task_id.isascii()
        or not all(character.isalnum() or character in "-_" for character in task_id)
    ):
        invalid("task_id must be a valid OmniHuman task identifier.")
    return task_id


def validate_options(prompt, enable_watermark):
    if not isinstance(prompt, str):
        invalid("prompt must be text.")
    if not isinstance(enable_watermark, bool):
        invalid("enable_watermark must be a boolean.")


def build_request(image_url, audio_url, prompt="", enable_watermark=True):
    validate_options(prompt, enable_watermark)
    body = {
        "req_key": REQ_KEY,
        "image_urls": [validate_url(image_url)],
        "audio_url": validate_url(audio_url),
        "enable_watermark": enable_watermark,
    }
    if prompt.strip():
        body["prompt"] = prompt.strip()
    return body


def redact_response(response, api_key=""):
    def redact(value):
        if isinstance(value, dict):
            return {
                redact(str(key)): "[redacted]" if _SECRET_PATTERN.search(str(key)) else redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, str):
            text = _URL_PATTERN.sub("[redacted URL]", value)
            if api_key:
                text = text.replace(api_key, "[redacted]")
            return text
        return value

    return redact(response)


class OmniHumanClient:
    def __init__(self, api_key):
        if not isinstance(api_key, str) or not api_key.strip():
            raise BytePlusException(get_text("err_no_omnihuman_key", path=credentials.env_file_path()))
        if not api_key.isascii() or not api_key.isprintable():
            invalid("Vision AI API key contains invalid header characters.")
        self.api_key = api_key
        self.base_url = OMNIHUMAN_BASE_URL

    def __repr__(self):
        return "OmniHumanClient(service='Vision AI')"


def get_omnihuman_client():
    return OmniHumanClient(credentials.get_setting(OMNIHUMAN_API_KEY_ENV))


async def _send(client, path, body):
    if path not in (SUBMIT_PATH, RESULT_PATH, CANCEL_PATH):
        invalid("Unsupported OmniHuman endpoint.")
    status, _, content = await send_http_request(
        "POST",
        client.base_url + path,
        {"Authorization": f"Bearer {client.api_key}", "Content-Type": "application/json"},
        body,
        REQUEST_TIMEOUT,
    )
    try:
        parsed = json.loads(content)
    except (ValueError, UnicodeError):
        parsed = None
    return status, parsed


async def request(client, path, body, *, cancel=False):
    try:
        status, response = await wait_interruptible(_send(client, path, body))
    except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as network_error:
        raise RetryableRequestError(
            get_text("err_omnihuman_network", reason=type(network_error).__name__),
            retryable=not isinstance(network_error, aiohttp.InvalidURL),
        ) from None
    if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
        raise BytePlusException(get_text("err_omnihuman_protocol", detail="Invalid HTTP status."))
    if not isinstance(response, dict) or type(response.get("code")) is not int:
        if status == 429 or status >= 500:
            raise RetryableRequestError(
                get_text("err_omnihuman_protocol", detail="Missing integer response code."),
                retryable=True,
                status=status,
            )
        raise BytePlusException(get_text("err_omnihuman_protocol", detail="Missing integer response code."))
    code = response["code"]
    if cancel and (200 <= status < 300 or status == 400) and code == 50217:
        return response
    if not 200 <= status < 300 or code != 10000:
        safe = redact_response(response, client.api_key)
        error = RetryableRequestError(
            get_text("err_omnihuman_api", status=status, code=code, message=str(safe.get("message", ""))[:2000]),
            retryable=code != 50510 and (status == 429 or status >= 500 or code in (50429, 50430, 50500, 50501)),
            status=status,
        )
        error.definitive_rejection = path == SUBMIT_PATH and 200 <= status < 500 and code != 10000
        raise error
    return response


async def submit_task(client, body):
    try:
        response = await request(client, SUBMIT_PATH, body)
        task_id = (response.get("data") or {}).get("task_id")
        validate_task_id(task_id)
        return task_id
    except Exception as error:
        if getattr(error, "definitive_rejection", False):
            raise
        raise BytePlusException(get_text("err_omnihuman_submit_unknown")) from None


async def poll_task(client, task_id):
    validate_task_id(task_id)
    response = await request(client, RESULT_PATH, {"req_key": REQ_KEY, "task_id": task_id})
    data = response.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("status"), str) or data["status"] not in TASK_STATES:
        raise BytePlusException(get_text("err_omnihuman_protocol", detail="Missing or unknown task status."))
    if data["status"] == "done":
        validate_url(data.get("video_url"))
    return response


async def wait_for_task(client, task_id, node_id=None):
    validate_task_id(task_id)
    try:
        while True:
            response = await retry_transient(
                lambda: poll_task(client, task_id), max_errors=MAX_POLL_ERRORS, poll_seconds=POLL_SECONDS
            )
            status = response["data"]["status"]
            if status == "done":
                return response
            if status not in ("in_queue", "generating"):
                raise BytePlusException(get_text("err_omnihuman_protocol", detail=f"Task is {status}."))
            send_node_text(node_id, get_text("omnihuman_waiting", task_id=task_id, status=status))
            await sleep_interruptible(POLL_SECONDS)
    except Exception as error:
        raise BytePlusException(
            get_text("err_omnihuman_task", task_id=task_id, detail=redact_response(str(error), client.api_key))
        ) from None


async def cancel_task(client, task_id):
    validate_task_id(task_id)
    response = await request(client, CANCEL_PATH, {"req_key": REQ_KEY, "task_id": task_id}, cancel=True)
    return response["code"] == 10000, redact_response(response, client.api_key)
