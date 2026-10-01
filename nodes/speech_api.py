import asyncio
import base64
import binascii
import json
import os
import re
import uuid

import aiohttp
from comfy_api.latest import io as comfy_io

from .constants import (
    DEFAULT_SPEECH_REGION,
    SPEECH_API_KEYS_CONSOLE_URL,
    SPEECH_ERROR_TEXT,
    SPEECH_REGION_BASE_URLS,
    SPEECH_POLL_MAX_ERRORS,
    SPEECH_REQUEST_TIMEOUT_SECONDS,
    SPEECH_SUCCESS_CODES,
)
from .nodes_shared import (
    LOG_PREFIX,
    ApiKeyStore,
    BytePlusException,
    get_text,
    sleep_interruptible,
    wait_interruptible,
)

# Seed Speech keys are a different product key from ModelArk keys, so they get
# their own file (git-ignored runtime file in the repo root) and socket type.
SPEECH_API_KEYS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "speech_api_keys.json"
)
SPEECH_API_KEY_STORE = ApiKeyStore(SPEECH_API_KEYS_FILE)

BytePlusSpeechClientType = comfy_io.Custom("BYTEPLUS_SPEECH_CLIENT")


class SeedSpeechClient:
    """
    Seed Speech API key and endpoint for the region. Passed between nodes on
    the BYTEPLUS_SPEECH_CLIENT socket; never serialized into outputs.
    """
    def __init__(self, api_key, region=DEFAULT_SPEECH_REGION):
        self.api_key = api_key
        self.region = region if region in SPEECH_REGION_BASE_URLS else DEFAULT_SPEECH_REGION
        self.base_url = SPEECH_REGION_BASE_URLS[self.region]

    def __repr__(self):
        return f"SeedSpeechClient(region={self.region!r})"


class SpeechResponse:
    def __init__(self, status, headers, body):
        self.status = int(status)
        self.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        self.body = body or b""

    @property
    def logid(self):
        return self.headers.get("x-tt-logid", "")

    @property
    def status_code(self):
        """X-Api-Status-Code (ASR), as int, or None."""
        return _as_int(self.headers.get("x-api-status-code"))

    def text(self):
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        try:
            return json.loads(self.body) if self.body else {}
        except ValueError:
            return None


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _plain(key, **kwargs):
    text = get_text(key, **kwargs)
    return text[len(LOG_PREFIX):] if text.startswith(LOG_PREFIX) else text


def require_speech_client(client):
    if not getattr(client, "api_key", None) or not getattr(client, "base_url", None):
        raise BytePlusException(get_text("speech_wrong_client"))
    return client


def describe_speech_error(code, message, status=None):
    """Readable text for a Seed Speech error code/message, or the message itself."""
    message = str(message or "").strip()
    lowered = message.lower()
    if status == 401 or code == 45000010 or ("api" in lowered and "key" in lowered and "invalid" in lowered):
        return _plain("speech_err_auth", url=SPEECH_API_KEYS_CONSOLE_URL)
    if "quota exceeded" in lowered and "concurrency" in lowered:
        return _plain("speech_err_concurrency")
    if code == 45000000 and "speaker" in lowered:
        return _plain("speech_err_speaker")
    if code == 40402003 or "exceededtextlimit" in lowered.replace(" ", ""):
        return _plain("speech_err_text_limit")
    if code == 55000031:
        return _plain("speech_err_busy")
    if code == 45000001:
        return f"{_plain('speech_err_params')}: {message}" if message else _plain("speech_err_params")
    if code == 45000002:
        return _plain("speech_err_empty_input_audio")
    if code == 45000151:
        return _plain("speech_err_audio_format")
    if code in SPEECH_ERROR_TEXT:
        text = SPEECH_ERROR_TEXT[code]
        return f"{text} ({message})" if message and message.lower() not in text.lower() else text
    return message or "no error message"


def speech_error(operation, response=None, code=None, message=None):
    """BytePlusException for a failed Seed Speech call (HTTP, body or header code)."""
    status = response.status if response is not None else 200
    if response is not None and (code is None or message is None):
        body = response.json()
        if isinstance(body, dict):
            nested = body.get("header") if isinstance(body.get("header"), dict) else body.get("error")
            source = nested if isinstance(nested, dict) else body
            if code is None:
                code = _as_int(source.get("code", source.get("status_code")))
            if message is None:
                message = source.get("message") or source.get("msg")
        if code is None:
            code = response.status_code
        if message is None:
            message = response.headers.get("x-api-message") or response.text()[:300]
    logid = response.logid if response is not None else ""
    return BytePlusException(get_text(
        "speech_request_failed",
        operation=operation,
        status=status,
        code=code if code is not None else "-",
        message=describe_speech_error(code, message, status),
        logid=f" (log ID: {logid})" if logid else "",
    ))


def b64decode_audio(data):
    try:
        return base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError, TypeError) as e:
        raise BytePlusException(get_text("speech_audio_decode_failed", e=e))


def check_code(operation, response, code, message=None):
    code = _as_int(code)
    if code is not None and code not in SPEECH_SUCCESS_CODES:
        raise speech_error(operation, response, code=code, message=message)


async def _send(method, url, headers, body, timeout):
    """One HTTP request; tests replace this function."""
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.request(method, url, headers=headers, json=body) as response:
            return SpeechResponse(response.status, dict(response.headers), await response.read())


class SpeechRequestError(BytePlusException):
    """A failed Seed Speech request. retryable: network errors, timeouts, 429 and 5xx."""

    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


async def speech_post(client, path, body, *, operation, headers=None,
                      timeout=SPEECH_REQUEST_TIMEOUT_SECONDS):
    """
    POST JSON to Seed Speech with the X-Api-Key header and a fresh
    X-Api-Request-Id (callers may override it). Raises BytePlusException for
    network errors and HTTP errors; the caller checks body/header codes.
    """
    require_speech_client(client)
    request_headers = {
        "X-Api-Key": client.api_key,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    request_headers.update(headers or {})
    try:
        response = await wait_interruptible(
            _send("POST", client.base_url + path, request_headers, body, timeout)
        )
    except asyncio.TimeoutError:
        raise SpeechRequestError(get_text("speech_timeout", operation=operation, seconds=timeout), retryable=True)
    except aiohttp.ClientError as e:
        raise SpeechRequestError(get_text("speech_network_error", operation=operation, e=e), retryable=True)
    if response.status >= 400:
        raise SpeechRequestError(
            str(speech_error(operation, response)), retryable=response.status == 429 or response.status >= 500
        )
    return response


async def speech_poll(client, path, body, *, operation, poll_seconds, headers=None):
    """
    speech_post for status queries of a submitted (possibly already billed)
    task: transient failures are retried after poll_seconds, up to
    SPEECH_POLL_MAX_ERRORS in a row, instead of abandoning the task.
    """
    errors = 0
    while True:
        try:
            return await speech_post(client, path, body, operation=operation, headers=headers)
        except SpeechRequestError as e:
            errors += 1
            if not e.retryable or errors >= SPEECH_POLL_MAX_ERRORS:
                raise
            await sleep_interruptible(poll_seconds)


async def download_bytes(url, operation, timeout=SPEECH_REQUEST_TIMEOUT_SECONDS):
    try:
        response = await wait_interruptible(_send("GET", url, {}, None, timeout))
    except asyncio.TimeoutError:
        raise BytePlusException(get_text("speech_timeout", operation=operation, seconds=timeout))
    except aiohttp.ClientError as e:
        raise BytePlusException(get_text("speech_network_error", operation=operation, e=e))
    if response.status >= 400:
        raise speech_error(operation, response, code=response.status, message=response.text()[:300])
    return response.body


def iter_json_objects(text):
    """
    JSON objects from a streamed body: newline-delimited, concatenated, or
    server-sent-event style ("data: {...}") lines.
    """
    text = re.sub(r"(?m)^\s*data:\s*", "", text)
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        while index < len(text) and text[index] not in "{[":
            index += 1
        if index >= len(text):
            return
        try:
            obj, index = decoder.raw_decode(text, index)
        except ValueError:
            # Failing beats returning silently truncated audio.
            raise BytePlusException(get_text("speech_stream_unreadable", position=index))
        if isinstance(obj, dict):
            yield obj
        elif isinstance(obj, list):
            yield from (item for item in obj if isinstance(item, dict))
