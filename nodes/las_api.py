import asyncio
import hashlib
import json
import math
import os
import re
import tempfile
import time
from collections.abc import Callable
from typing import TypedDict
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp
import comfy.model_management

from . import credentials
from .constants import (
    DEFAULT_LAS_REGION,
    LAS_MAX_POLL_ERRORS,
    LAS_POLL_SECONDS,
    LAS_REGION_BASE_URLS,
    LAS_REQUEST_TIMEOUT_SECONDS,
    LAS_TASK_RETENTION_SECONDS,
)
from .http_requests import RetryableRequestError, is_retryable_status, retry_transient, send_http_request
from .nodes_shared import (
    BytePlusException,
    get_text,
    keyed_lock,
    send_node_text,
    sleep_interruptible,
    wait_interruptible,
)


class LASTaskMetadata(TypedDict, total=False):
    task_id: str
    task_status: str
    business_code: str | int
    error_msg: str
    operator_id: str
    operator_version: str


class LASTaskHandle(TypedDict):
    node_id: str
    task_id: str
    operator_id: str
    operator_version: str
    credential_fingerprint: str
    request_fingerprint: str
    accepted_at: float
    status: str


LAS_TASK_HANDLES: dict[tuple[str, str, str], LASTaskHandle] = {}
LAS_TASK_LOCKS: dict = {}
_TASK_HANDLES_LOADED = False
_OPERATORS = frozenset(("las_video_edit_enhance", "las_video_translate"))
_TASK_STATES = frozenset(("PENDING", "RUNNING", "COMPLETED", "FAILED", "TIMEOUT"))
_GENERATION_STATES = frozenset(("queued", "running", "succeeded", "failed", "expired"))
_URL_PATTERN = re.compile(r"(?:https?|tos)://[^\s<>\"']+", re.IGNORECASE)
_SECRET_FIELD_PATTERN = re.compile(
    r"api.?key|access.?key|secret|authorization|credential|password|token|signature|presigned|signed.?url",
    re.IGNORECASE,
)


class LASClient:
    def __init__(self, api_key: str, region: str = DEFAULT_LAS_REGION):
        if not isinstance(api_key, str) or not api_key.strip():
            raise BytePlusException(get_text("err_no_las_key", path=credentials.env_file_path()))
        if not api_key.isprintable():
            raise BytePlusException(
                get_text("err_las_protocol", detail="LAS API key contains invalid header characters.")
            )
        if not isinstance(region, str) or region not in LAS_REGION_BASE_URLS:
            raise BytePlusException(get_text("err_las_region", region=_redact_text(str(region), api_key)))
        self.api_key = api_key
        self.region = region
        self.base_url = LAS_REGION_BASE_URLS[region]

    def __repr__(self) -> str:
        return f"LASClient(region={self.region!r})"


def get_las_client() -> LASClient:
    api_key = credentials.get_setting(*credentials.LAS_API_KEY_ENVS)
    if not api_key:
        raise BytePlusException(get_text("err_no_las_key", path=credentials.env_file_path()))
    return LASClient(api_key)


def _redact_url(match: re.Match) -> str:
    try:
        parsed = urlsplit(match.group())
        return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))
    except ValueError:
        return "[redacted URL]"


def _redact_text(value: str, api_key: str | None = None, secrets: tuple[str, ...] = ()) -> str:
    text = _URL_PATTERN.sub(_redact_url, value)
    for secret in (api_key, *secrets):
        if not secret:
            continue
        for encoded in (secret, quote(secret, safe=""), json.dumps(secret)[1:-1]):
            text = text.replace(encoded, "[redacted]")
    return text


def redact_las_response(response: dict, api_key: str | None = None, *, secrets: tuple[str, ...] = ()) -> dict:
    api_key = api_key if api_key is not None else credentials.get_setting(*credentials.LAS_API_KEY_ENVS)

    def redact(value):
        if isinstance(value, dict):
            return {
                _redact_text(str(key), api_key, secrets): "[redacted]"
                if _SECRET_FIELD_PATTERN.search(str(key))
                else redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, str):
            return _redact_text(value, api_key, secrets)
        return value

    return redact(response)


def _message(client: LASClient, key: str, **values) -> str:
    safe_values = {name: _redact_text(str(value), client.api_key)[:2000] for name, value in values.items()}
    return _redact_text(get_text(key, **safe_values), client.api_key)


def _protocol(client: LASClient, detail: str, task_id: str | None = None) -> BytePlusException:
    if task_id:
        detail = f"Task {task_id}: {detail}"
    return BytePlusException(_message(client, "err_las_protocol", detail=detail))


def _valid_identifier(value) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 512
        and value.isprintable()
        and not any(character.isspace() for character in value)
    )


def _operator(client: LASClient, operator_id: str, operator_version: str):
    if not isinstance(operator_id, str) or operator_id not in _OPERATORS:
        raise _protocol(client, "Unsupported operator_id.")
    if operator_version != "v1":
        raise _protocol(client, "Unsupported operator_version.")


def _canonical_request(client: LASClient, envelope: dict) -> str:
    if not isinstance(envelope, dict):
        raise _protocol(client, "Submit envelope must be an object.")
    _operator(client, envelope.get("operator_id"), envelope.get("operator_version"))
    if not isinstance(envelope.get("data"), dict):
        raise _protocol(client, "Submit data must be an object.")
    if set(envelope) - {"operator_id", "operator_version", "data", "callback"}:
        raise _protocol(client, "Unexpected submit envelope fields.")
    try:
        return json.dumps(envelope, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise _protocol(client, "Submit envelope must contain finite JSON values.") from None


async def _send(
    client: LASClient,
    method: str,
    path: str,
    body: dict | None = None,
    timeout_seconds: float = LAS_REQUEST_TIMEOUT_SECONDS,
) -> tuple[int, dict | None]:
    if method != "POST" or path not in ("/api/v1/submit", "/api/v1/poll"):
        raise _protocol(client, "Unsupported LAS endpoint or method.")
    status, _, content = await send_http_request(
        method,
        client.base_url + path,
        {"Authorization": f"Bearer {client.api_key}", "Content-Type": "application/json"},
        body,
        timeout_seconds,
    )
    try:
        parsed = json.loads(content) if content else None
    except (ValueError, UnicodeError):
        parsed = None
    return status, parsed


def _api_detail(client: LASClient, data) -> tuple[str, str]:
    if not isinstance(data, dict):
        return "Error", ""
    data = redact_las_response(data, client.api_key)
    metadata = data.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    error = data.get("error")
    error = error if isinstance(error, dict) else {}
    return str(metadata.get("business_code") or error.get("code") or "Error"), str(
        metadata.get("error_msg") or error.get("message") or ""
    )


async def las_request(
    client: LASClient, path: str, body: dict, timeout_seconds: float = LAS_REQUEST_TIMEOUT_SECONDS
) -> dict:
    if path not in ("/api/v1/submit", "/api/v1/poll"):
        raise _protocol(client, "Unsupported LAS endpoint.")
    try:
        status, data = await wait_interruptible(_send(client, "POST", path, body, timeout_seconds=timeout_seconds))
    except asyncio.TimeoutError:
        raise RetryableRequestError(
            _message(client, "err_las_timeout", seconds=timeout_seconds), retryable=True
        ) from None
    except (aiohttp.ClientError, OSError) as error:
        raise RetryableRequestError(_message(client, "err_las_network", e=error), retryable=True) from None
    if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
        raise _protocol(client, "Invalid HTTP status.")
    if not 200 <= status < 300:
        code, message = _api_detail(client, data)
        raise RetryableRequestError(
            _message(client, "err_las_api", status=status, code=code, message=message),
            retryable=is_retryable_status(status),
            status=status,
        ) from None
    if not isinstance(data, dict):
        raise RetryableRequestError(
            _message(client, "err_las_protocol", detail="Response is not a JSON object."),
            retryable=False,
            status=status,
        ) from None
    return data


def _validate_identity(client: LASClient, metadata: dict, operator_id: str, operator_version: str, task_id: str | None):
    for field, expected in (("operator_id", operator_id), ("operator_version", operator_version), ("task_id", task_id)):
        if field in metadata and expected is not None and metadata[field] != expected:
            raise _protocol(client, f"Returned {field} does not match the request.", task_id)


def _validate_task(
    client: LASClient, task: dict, operator_id: str, operator_version: str, task_id: str | None = None
) -> dict:
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        raise _protocol(client, "Missing task metadata.", task_id)
    if task_id is None:
        task_id = metadata.get("task_id")
        if not _valid_identifier(task_id):
            raise _protocol(client, "Missing or invalid accepted task_id.")
    _validate_identity(client, metadata, operator_id, operator_version, task_id)
    status = metadata.get("task_status")
    if not isinstance(status, str) or status not in _TASK_STATES:
        raise _protocol(client, "Unknown or missing task_status.", task_id)
    code = metadata.get("business_code")
    if type(code) not in (str, int) or (isinstance(code, str) and not code):
        raise _protocol(client, "Missing or invalid business_code.", task_id)
    error_msg = metadata.get("error_msg", "")
    if not isinstance(error_msg, str):
        raise _protocol(client, "Invalid error_msg.", task_id)
    if code not in (0, "0") or status in ("FAILED", "TIMEOUT"):
        raise BytePlusException(
            _message(client, "err_las_task_failed", task_id=task_id, status=status, detail=f"{code}: {error_msg}")
        ) from None
    data = task.get("data")
    if data is not None and not isinstance(data, dict):
        raise _protocol(client, "Task data must be an object.", task_id)
    if operator_id == "las_video_edit_enhance" and isinstance(data, dict) and "status" in data:
        nested_status = data["status"]
        if not isinstance(nested_status, str) or nested_status not in _GENERATION_STATES:
            raise _protocol(client, "Unknown nested generation status.", task_id)
        if nested_status in ("failed", "expired"):
            nested_code, nested_message = _api_detail(client, data)
            raise BytePlusException(
                _message(
                    client,
                    "err_las_task_failed",
                    task_id=task_id,
                    status=nested_status,
                    detail=f"{nested_code}: {nested_message}",
                )
            ) from None
    if status != "COMPLETED":
        return task
    if not isinstance(data, dict):
        raise _protocol(client, "Completed task has no result data.", task_id)
    if operator_id == "las_video_edit_enhance":
        content = data.get("content")
        if data.get("status") != "succeeded" or not isinstance(content, dict):
            raise _protocol(client, "Completed task has no succeeded generation.", task_id)
        if not isinstance(content.get("video_url"), str) or not content["video_url"].strip():
            raise _protocol(client, "Completed task has no video_url.", task_id)
    else:
        if not isinstance(data.get("translated_videos"), list):
            raise _protocol(client, "Completed translation has no translated_videos array.", task_id)
        for field in ("translated_videos", "translated_captions", "repaired_videos"):
            if field in data and (
                not isinstance(data[field], list) or any(not isinstance(item, dict) for item in data[field])
            ):
                raise _protocol(client, f"Invalid {field} array.", task_id)
    return task


async def _submit(
    client: LASClient,
    envelope: dict,
    on_accepted: Callable | None = None,
    node_id: str | None = None,
) -> dict:
    _canonical_request(client, envelope)
    try:
        task = await las_request(client, "/api/v1/submit", envelope)
    except (comfy.model_management.InterruptProcessingException, asyncio.CancelledError) as error:
        message = _message(client, "err_las_submit_unknown", detail="Interrupted before task acceptance was confirmed.")
        send_node_text(node_id, message)
        error.args = (message,)
        raise
    except RetryableRequestError as error:
        if error.status is None or error.status >= 500 or 200 <= error.status < 300:
            raise RetryableRequestError(
                _message(client, "err_las_submit_unknown", detail=error),
                retryable=error.retryable,
                status=error.status,
            ) from None
        raise
    metadata = task.get("metadata")
    task_id = metadata.get("task_id") if isinstance(metadata, dict) else None
    if _valid_identifier(task_id):
        _validate_identity(client, metadata, envelope["operator_id"], envelope["operator_version"], task_id)
        if on_accepted:
            on_accepted(task_id, metadata.get("task_status"))
    else:
        raise BytePlusException(
            _message(client, "err_las_submit_unknown", detail="Response contained no valid accepted task_id.")
        ) from None
    return _validate_task(client, task, envelope["operator_id"], envelope["operator_version"])


async def submit_las_task(client: LASClient, envelope: dict) -> dict:
    return await _submit(client, envelope)


async def poll_las_task(client: LASClient, operator_id: str, task_id: str, operator_version: str = "v1") -> dict:
    _operator(client, operator_id, operator_version)
    if not _valid_identifier(task_id):
        raise _protocol(client, "task_id must be a nonempty identifier.")
    task = await las_request(
        client, "/api/v1/poll", {"operator_id": operator_id, "operator_version": operator_version, "task_id": task_id}
    )
    _validate_task(client, task, operator_id, operator_version, task_id)
    if "task_id" not in task["metadata"]:
        return {**task, "metadata": {**task["metadata"], "task_id": task_id}}
    return task


def _credential_fingerprint(client: LASClient) -> str:
    return hashlib.sha256(json.dumps([client.region, client.api_key], separators=(",", ":")).encode()).hexdigest()


def _task_handles_path() -> str:
    return os.path.join(os.path.dirname(credentials.env_file_path()), "las_task_handles.json")


def _handle_is_fresh(handle: dict, now: float | None = None) -> bool:
    accepted_at = handle.get("accepted_at")
    if not isinstance(accepted_at, (int, float)) or isinstance(accepted_at, bool) or not math.isfinite(accepted_at):
        return False
    now = time.time() if now is None else now
    return accepted_at + LAS_TASK_RETENTION_SECONDS > now


def _valid_handle(handle: dict) -> bool:
    return (
        isinstance(handle, dict)
        and _valid_identifier(handle.get("node_id"))
        and _valid_identifier(handle.get("task_id"))
        and handle.get("operator_id") in _OPERATORS
        and handle.get("operator_version") == "v1"
        and isinstance(handle.get("credential_fingerprint"), str)
        and len(handle["credential_fingerprint"]) == 64
        and isinstance(handle.get("request_fingerprint"), str)
        and len(handle["request_fingerprint"]) == 64
        and isinstance(handle.get("status"), str)
        and handle["status"] in (_TASK_STATES | {"UNKNOWN"})
        and _handle_is_fresh(handle)
    )


def _persist_task_handles():
    entries = [handle for handle in LAS_TASK_HANDLES.values() if _valid_handle(handle)]
    path = _task_handles_path()
    if not entries:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle, temp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix="las-task-handles.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as temp:
            json.dump({"version": 1, "handles": entries}, temp, separators=(",", ":"), sort_keys=True)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _load_task_handles(force: bool = False):
    global _TASK_HANDLES_LOADED
    if _TASK_HANDLES_LOADED and LAS_TASK_HANDLES and not force:
        return
    LAS_TASK_HANDLES.clear()
    path = _task_handles_path()
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        _TASK_HANDLES_LOADED = True
        return
    except (OSError, ValueError, TypeError):
        _TASK_HANDLES_LOADED = True
        return
    handles = payload.get("handles") if isinstance(payload, dict) else None
    if not isinstance(handles, list):
        _TASK_HANDLES_LOADED = True
        return
    for item in handles:
        if not _valid_handle(item):
            continue
        key = (item["node_id"], item["credential_fingerprint"], item["operator_id"])
        existing = LAS_TASK_HANDLES.get(key)
        if existing is None or item["accepted_at"] >= existing["accepted_at"]:
            LAS_TASK_HANDLES[key] = item
    _TASK_HANDLES_LOADED = True
    _persist_task_handles()


def _update_handle(client: LASClient, operator_id: str, task_id: str, status: str):
    _load_task_handles()
    fingerprint = _credential_fingerprint(client)
    for handle in LAS_TASK_HANDLES.values():
        if (
            handle["credential_fingerprint"] == fingerprint
            and handle["operator_id"] == operator_id
            and handle["task_id"] == task_id
        ):
            handle["status"] = status
            _persist_task_handles()
            return


async def wait_for_las_task(
    client: LASClient,
    operator_id: str,
    task_id: str,
    node_id: str | None = None,
    deadline: float | None = None,
    operator_version: str = "v1",
) -> dict:
    _operator(client, operator_id, operator_version)
    if not _valid_identifier(task_id):
        raise _protocol(client, "task_id must be a nonempty identifier.")
    started = time.monotonic()
    deadline = time.time() + LAS_TASK_RETENTION_SECONDS if deadline is None else deadline
    if not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or not math.isfinite(deadline):
        raise _protocol(client, "deadline must be a finite timestamp.", task_id)
    while True:
        comfy.model_management.throw_exception_if_processing_interrupted()
        remaining = deadline - time.time()
        if remaining <= 0:
            raise BytePlusException(_message(client, "err_las_deadline", task_id=task_id)) from None
        try:
            task = await wait_interruptible(
                asyncio.wait_for(
                    retry_transient(
                        lambda: poll_las_task(client, operator_id, task_id, operator_version),
                        max_errors=LAS_MAX_POLL_ERRORS,
                        poll_seconds=min(LAS_POLL_SECONDS, remaining),
                    ),
                    timeout=remaining,
                )
            )
        except asyncio.TimeoutError:
            raise BytePlusException(_message(client, "err_las_deadline", task_id=task_id)) from None
        except RetryableRequestError as error:
            raise BytePlusException(_message(client, "err_las_poll_failed", task_id=task_id, detail=error)) from None
        status = task["metadata"]["task_status"]
        _update_handle(client, operator_id, task_id, status)
        if status == "COMPLETED":
            return task
        send_node_text(
            node_id,
            _message(
                client, "las_task_waiting", task_id=task_id, status=status, elapsed=int(time.monotonic() - started)
            ),
        )
        await sleep_interruptible(min(LAS_POLL_SECONDS, max(0, deadline - time.time())))


def _handle_identity(
    client: LASClient, envelope: dict, node_id: str, rerun_seed: int
) -> tuple[tuple[str, str, str], str]:
    normalized = _canonical_request(client, envelope)
    if not isinstance(node_id, (str, int)) or isinstance(node_id, bool) or not str(node_id):
        raise _protocol(client, "A node_id is required to safely resume accepted tasks.")
    if not isinstance(rerun_seed, int) or isinstance(rerun_seed, bool):
        raise _protocol(client, "rerun_seed must be an integer.")
    credential_fingerprint = _credential_fingerprint(client)
    request_fingerprint = hashlib.sha256(f"{rerun_seed}:{normalized}".encode()).hexdigest()
    return (str(node_id), credential_fingerprint, envelope["operator_id"]), request_fingerprint


async def submit_and_wait(client: LASClient, envelope: dict, node_id: str, rerun_seed: int = 0) -> dict:
    _load_task_handles()
    key, request_fingerprint = _handle_identity(client, envelope, node_id, rerun_seed)
    async with keyed_lock(LAS_TASK_LOCKS, key[0]):
        for other_key, other_handle in LAS_TASK_HANDLES.items():
            if other_key[0] == key[0] and (
                other_key != key or other_handle["request_fingerprint"] != request_fingerprint
            ):
                raise BytePlusException(
                    _message(client, "err_las_resume_changed", task_id=other_handle["task_id"])
                ) from None
        handle = LAS_TASK_HANDLES.get(key)
        if handle is None:

            def record(task_id, status):
                LAS_TASK_HANDLES[key] = {
                    "node_id": key[0],
                    "task_id": task_id,
                    "operator_id": envelope["operator_id"],
                    "operator_version": envelope["operator_version"],
                    "credential_fingerprint": key[1],
                    "request_fingerprint": request_fingerprint,
                    "accepted_at": time.time(),
                    "status": status if isinstance(status, str) and status in _TASK_STATES else "UNKNOWN",
                }
                _persist_task_handles()
                send_node_text(node_id, _message(client, "las_task_submitted", task_id=task_id))

            submitted = await _submit(client, envelope, record, node_id)
            handle = LAS_TASK_HANDLES[key]
            if submitted["metadata"]["task_status"] == "COMPLETED":
                return submitted
        else:
            send_node_text(
                node_id,
                _message(client, "las_task_resuming", task_id=handle["task_id"], status=handle["status"].lower()),
            )
        return await wait_for_las_task(
            client,
            handle["operator_id"],
            handle["task_id"],
            node_id=node_id,
            deadline=handle["accepted_at"] + LAS_TASK_RETENTION_SECONDS,
            operator_version=handle["operator_version"],
        )


def clear_las_task_handle(client: LASClient, envelope: dict, node_id: str, rerun_seed: int = 0) -> bool:
    _load_task_handles()
    key, request_fingerprint = _handle_identity(client, envelope, node_id, rerun_seed)
    handle = LAS_TASK_HANDLES.get(key)
    if (
        handle is None
        or handle["request_fingerprint"] != request_fingerprint
        or handle["status"] != "COMPLETED"
        or key[0] in LAS_TASK_LOCKS
    ):
        return False
    del LAS_TASK_HANDLES[key]
    _persist_task_handles()
    return True
