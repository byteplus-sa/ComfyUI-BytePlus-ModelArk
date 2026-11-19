import hashlib
import json
import os
import tempfile

import folder_paths
from comfy_api.latest import io as comfy_io

from . import credentials
from .constants import OMNIHUMAN_API_KEY_ENV
from .nodes_shared import GLOBAL_CATEGORY, BytePlusException, get_text, keyed_lock, log_msg, send_node_text, with_client
from .omnihuman_api import (
    build_request,
    cancel_task,
    get_omnihuman_client,
    poll_task,
    redact_response,
    submit_task,
    validate_options,
    validate_task_id,
    wait_for_task,
)
from .omnihuman_media import collect_video, prepare_media, upload_media

_TASK_LOCKS = {}
_CATEGORY = f"{GLOBAL_CATEGORY}/OmniHuman"


def handle_directory():
    return os.path.join(folder_paths.get_user_directory(), "byteplus", "omnihuman_tasks")


def _handle_path(node_id):
    return os.path.join(handle_directory(), hashlib.sha256(str(node_id).encode()).hexdigest() + ".json")


def _read_handle(node_id):
    path = _handle_path(node_id)
    try:
        with open(path, encoding="utf-8") as file:
            data = json.loads(file.read(16384))
        if (
            not isinstance(data, dict)
            or set(data) != {"credential_hash", "request_hash", "task_id"}
            or any(
                not isinstance(data.get(field), str)
                or len(data[field]) != 64
                or any(character not in "0123456789abcdef" for character in data[field])
                for field in ("credential_hash", "request_hash")
            )
        ):
            raise ValueError("Invalid recovery data")
        if data["task_id"] is not None:
            validate_task_id(data["task_id"])
        return data
    except FileNotFoundError:
        return None
    except (OSError, ValueError, BytePlusException):
        raise BytePlusException(get_text("err_omnihuman_handle")) from None


def _write_handle(node_id, handle):
    path = _handle_path(node_id)
    temporary = None
    try:
        os.makedirs(handle_directory(), mode=0o700, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=handle_directory(), prefix=".omnihuman-")
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(handle, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except OSError:
        raise BytePlusException(get_text("err_omnihuman_handle")) from None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _clear_handle(node_id):
    try:
        os.unlink(_handle_path(node_id))
    except FileNotFoundError:
        return
    except OSError:
        raise BytePlusException(get_text("err_omnihuman_handle")) from None


def _request_hash(prepared, prompt, enable_watermark, seed):
    digest = hashlib.sha256()
    for value in (
        prepared.image_bytes if prepared.image_bytes is not None else prepared.image_url.encode(),
        prepared.audio_bytes if prepared.audio_bytes is not None else prepared.audio_url.encode(),
        json.dumps([prompt.strip(), enable_watermark, seed], allow_nan=False).encode(),
    ):
        digest.update(hashlib.sha256(value).digest())
    return digest.hexdigest()


def _outputs():
    return [
        comfy_io.Video.Output("video"),
        comfy_io.String.Output("task_id", tooltip="Keep this ID for OmniHuman Task Result recovery."),
        comfy_io.String.Output("response", tooltip="Provider response with keys and URLs redacted."),
    ]


def _hidden():
    return [
        comfy_io.Hidden.unique_id,
        comfy_io.Hidden.auth_token_comfy_org,
        comfy_io.Hidden.api_key_comfy_org,
    ]


class BytePlusOmniHuman(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return comfy_io.Schema(
            node_id="BytePlusOmniHuman",
            display_name="BytePlus OmniHuman 2.0",
            category=_CATEGORY,
            description="Generate an avatar video from one image and speech audio using OmniHuman 2.0. Requires a separate Vision AI key. Paid work; use only authorized media. Connected media needs Comfy.org storage. Interruption stops local waiting, not remote processing.",
            inputs=[
                comfy_io.String.Input(
                    "prompt", default="", multiline=True, tooltip="Optional scene, movement and camera guidance."
                ),
                comfy_io.Boolean.Input("enable_watermark", default=True),
                comfy_io.Int.Input(
                    "seed",
                    default=0,
                    min=0,
                    max=0xFFFFFFFFFFFFFFFF,
                    control_after_generate=comfy_io.ControlAfterGenerate.fixed,
                    tooltip="Local rerun control only, not sent to OmniHuman. Keep fixed to resume an unresolved task.",
                ),
                comfy_io.Image.Input("image", optional=True, tooltip="Exactly one image, or use image_url."),
                comfy_io.Audio.Input(
                    "audio",
                    optional=True,
                    tooltip="One 4-300 second speech clip (up to 5 minutes), or use audio_url. Oversized WAVs are converted to mono at up to 24 kHz without truncation.",
                ),
                comfy_io.String.Input(
                    "image_url",
                    default="",
                    optional=True,
                    tooltip="Persistent public image URL, instead of a connected image.",
                ),
                comfy_io.String.Input(
                    "audio_url",
                    default="",
                    optional=True,
                    tooltip="Persistent public WAV/MP3 URL, instead of connected audio.",
                ),
                comfy_io.String.Input(
                    "existing_task_id",
                    default="",
                    optional=True,
                    advanced=True,
                    tooltip="Recover this task without generation or uploads. Other generation inputs are ignored. Must match any unresolved task and its key.",
                ),
            ],
            outputs=_outputs(),
            hidden=_hidden(),
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        key_hash = hashlib.sha256(credentials.get_setting(OMNIHUMAN_API_KEY_ENV).encode()).hexdigest()
        if kwargs.get("existing_task_id") or os.path.exists(_handle_path(cls.hidden.unique_id)):
            return float("nan")
        return key_hash

    @classmethod
    @with_client("omnihuman_client", get_omnihuman_client)
    async def execute(
        cls,
        omnihuman_client,
        prompt="",
        enable_watermark=True,
        seed=0,
        image=None,
        audio=None,
        image_url="",
        audio_url="",
        existing_task_id="",
    ):
        node_id = cls.hidden.unique_id
        if not isinstance(node_id, str) or not node_id:
            raise BytePlusException(
                get_text("err_omnihuman_input", detail="A unique node ID is required for recovery.")
            )
        if not isinstance(existing_task_id, str):
            raise BytePlusException(get_text("err_omnihuman_input", detail="existing_task_id must be text."))
        existing_task_id = existing_task_id.strip()
        key_hash = hashlib.sha256(omnihuman_client.api_key.encode()).hexdigest()
        async with keyed_lock(_TASK_LOCKS, node_id):
            handle = _read_handle(node_id)
            if existing_task_id:
                task_id = validate_task_id(existing_task_id)
                if handle and (handle["credential_hash"] != key_hash or handle["task_id"] not in (None, task_id)):
                    raise BytePlusException(
                        get_text("err_omnihuman_unresolved", task_id=handle["task_id"] or "acceptance unknown")
                    )
                handle = (
                    {**handle, "task_id": task_id}
                    if handle
                    else {
                        "credential_hash": key_hash,
                        "request_hash": hashlib.sha256(task_id.encode()).hexdigest(),
                        "task_id": task_id,
                    }
                )
                _write_handle(node_id, handle)
            else:
                validate_options(prompt, enable_watermark)
                if type(seed) is not int or not 0 <= seed <= 0xFFFFFFFFFFFFFFFF:
                    raise BytePlusException(
                        get_text("err_omnihuman_input", detail="seed must be an unsigned 64-bit integer.")
                    )
                prepared = await prepare_media(
                    image, audio, "" if image_url is None else image_url, "" if audio_url is None else audio_url
                )
                request_hash = _request_hash(prepared, prompt, enable_watermark, seed)
                if handle and (
                    handle["credential_hash"] != key_hash
                    or handle["request_hash"] != request_hash
                    or not handle["task_id"]
                ):
                    raise BytePlusException(
                        get_text("err_omnihuman_unresolved", task_id=handle["task_id"] or "acceptance unknown")
                    )
                if not handle:
                    urls = await upload_media(cls, prepared)
                    body = build_request(*urls, prompt=prompt, enable_watermark=enable_watermark)
                    handle = {"credential_hash": key_hash, "request_hash": request_hash, "task_id": None}
                    _write_handle(node_id, handle)
                    try:
                        handle["task_id"] = await submit_task(omnihuman_client, body)
                    except Exception as error:
                        if getattr(error, "definitive_rejection", False):
                            _clear_handle(node_id)
                        raise
                    try:
                        _write_handle(node_id, handle)
                    except BytePlusException as error:
                        raise BytePlusException(
                            get_text("err_omnihuman_task", task_id=handle["task_id"], detail=str(error))
                        ) from None
                    log_msg("omnihuman_submitted", task_id=handle["task_id"])
                task_id = handle["task_id"]
            send_node_text(node_id, get_text("omnihuman_submitted", task_id=task_id))
            task = await wait_for_task(omnihuman_client, task_id, node_id)
            video, complete = await collect_video(task, task_id)
            if complete:
                _clear_handle(node_id)
            return comfy_io.NodeOutput(video, task_id, json.dumps(redact_response(task, omnihuman_client.api_key)))


class BytePlusOmniHumanTaskResult(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return comfy_io.Schema(
            node_id="BytePlusOmniHumanTaskResult",
            display_name="BytePlus OmniHuman Task Result",
            category=_CATEGORY,
            description="Collect an existing OmniHuman 2.0 task with its original account's Vision AI key. Never submits new generation. Task lookup lasts about 12 hours; video links expire after one hour.",
            inputs=[
                comfy_io.String.Input("task_id", default=""),
                comfy_io.Boolean.Input("wait_for_completion", default=True),
            ],
            outputs=_outputs(),
            hidden=[comfy_io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    @with_client("omnihuman_client", get_omnihuman_client)
    async def execute(cls, omnihuman_client, task_id, wait_for_completion=True):
        validate_task_id(task_id)
        if not isinstance(wait_for_completion, bool):
            raise BytePlusException(get_text("err_omnihuman_input", detail="wait_for_completion must be a boolean."))
        task = (
            await wait_for_task(omnihuman_client, task_id, cls.hidden.unique_id)
            if wait_for_completion
            else await poll_task(omnihuman_client, task_id)
        )
        video, _ = await collect_video(task, task_id)
        return comfy_io.NodeOutput(video, task_id, json.dumps(redact_response(task, omnihuman_client.api_key)))


class BytePlusOmniHumanCancelTask(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return comfy_io.Schema(
            node_id="BytePlusOmniHumanCancelTask",
            display_name="BytePlus OmniHuman Cancel Task",
            category=_CATEGORY,
            description="Explicitly request cancellation of an OmniHuman 2.0 task. Only queued tasks can be canceled; running tasks may continue and be billed. Connect an output to execute.",
            inputs=[comfy_io.String.Input("task_id", default="")],
            outputs=[comfy_io.Boolean.Output("canceled"), comfy_io.String.Output("response")],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    @with_client("omnihuman_client", get_omnihuman_client)
    async def execute(cls, omnihuman_client, task_id):
        canceled, response = await cancel_task(omnihuman_client, task_id)
        if not canceled:
            log_msg("omnihuman_cancel_unavailable", task_id=task_id)
        return comfy_io.NodeOutput(canceled, json.dumps(response))


NODES = [BytePlusOmniHuman, BytePlusOmniHumanTaskResult, BytePlusOmniHumanCancelTask]
