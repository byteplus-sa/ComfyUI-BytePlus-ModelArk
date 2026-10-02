import os
import io
import base64
import hashlib
import json
import re
import folder_paths
import time
import numpy
import PIL.Image
import torch
import torch.nn.functional as F
import requests
import asyncio
import threading
import comfy.model_management
from byteplussdkarkruntime import Ark

from comfy_api.latest import io as comfy_io

import logging

from .constants import MESSAGES, ERROR_TEXT_MATCH_RULES, REGION_BASE_URLS, DEFAULT_REGION

LOG_PREFIX = "[BytePlus] "

def patch_log_messages():
    """
    Prefix console messages with LOG_PREFIX.
    """
    ignore_keys = {"api_errors"}

    for key, value in MESSAGES.items():
        if key in ignore_keys or key.startswith("est_"):
            continue

        if isinstance(value, str):
            if value.strip().startswith("-"):
                continue

            if value.startswith("\n"):
                if LOG_PREFIX.strip() not in value:
                    MESSAGES[key] = "\n" + LOG_PREFIX + value[1:]
            else:
                if not value.startswith(LOG_PREFIX):
                    MESSAGES[key] = LOG_PREFIX + value

patch_log_messages()

logger = logging.getLogger("BytePlus")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

GLOBAL_CATEGORY = "BytePlus ModelArk"

byteplus_api_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_KEYS_FILE = os.path.join(byteplus_api_dir, "api_keys.json")
FILES_UPLOAD_CACHE_FILE = os.path.join(byteplus_api_dir, "files_upload_cache.json")

BytePlusClientType = comfy_io.Custom("BYTEPLUS_CLIENT")


class ApiKeyStore:
    def __init__(self, config_file):
        self.config_file = config_file
        self._lock = threading.RLock()
        self._items = []

    def load(self):
        loaded_items = []

        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "r", encoding="utf-8") as f:
                    keys_data = json.load(f)
                if isinstance(keys_data, list):
                    for item in keys_data:
                        if "customName" in item and "apiKey" in item:
                            loaded_items.append(item)
        except Exception as e:
            log_msg("api_load_error", e=e)

        with self._lock:
            self._items = loaded_items

    def save(self):
        with self._lock:
            serializable_items = list(self._items)
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(serializable_items, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save API key: {e}")
            return False
        return True

    def upsert(self, name, key, access_key="", secret_key=""):
        """
        Add or update a key entry. IAM AK/SK (asset library) are stored with the
        entry only when both are given; an update without them keeps the ones
        already saved. New AK/SK replace an old session token, which belonged
        to the old pair.
        """
        with self._lock:
            entry = next((item for item in self._items if item["customName"] == name), None)
            if entry is None:
                entry = {"customName": name, "apiKey": key}
                self._items.append(entry)
            entry["apiKey"] = key
            if access_key and secret_key:
                entry["accessKey"] = access_key
                entry["secretKey"] = secret_key
                entry.pop("sessionToken", None)

        return self.save()

    def get_items(self):
        with self._lock:
            return [dict(item) for item in self._items]

    def get_key_names(self):
        with self._lock:
            return [item["customName"] for item in self._items]

    def find_api_key(self, key_name):
        with self._lock:
            for item in self._items:
                if item["customName"] == key_name:
                    return item["apiKey"]
        return None

    def find_asset_credentials(self, key_name):
        """
        Optional IAM AK/SK stored with a key entry ("accessKey", "secretKey",
        "sessionToken"); needed only for the asset library OpenAPI.
        """
        with self._lock:
            for item in self._items:
                if item["customName"] == key_name and item.get("accessKey") and item.get("secretKey"):
                    return {
                        "access_key": item["accessKey"],
                        "secret_key": item["secretKey"],
                        "session_token": item.get("sessionToken") or "",
                    }
        return None


API_KEY_STORE = ApiKeyStore(API_KEYS_FILE)


def get_text(key, **kwargs):
    """
    Return the message for key, formatted with kwargs when given.
    """
    msg = MESSAGES.get(key, key)
    if kwargs:
        try:
            return msg.format(**kwargs)
        except:
            pass
    return msg


def log_msg(key, default_msg="", **kwargs):
    """
    Log the message for key.
    """
    msg = MESSAGES.get(key, default_msg)
    if msg:
        raw_api_response = kwargs.pop("raw_api_response", None)
        rendered_msg = msg
        try:
            rendered_msg = msg.format(**kwargs)
        except:
            pass
        logger.info(rendered_msg)
        if any(code in str(rendered_msg) for code in ("InvalidParameter", "MissingParameter")):
            if raw_api_response is None:
                raw_api_response = kwargs.get("e")
            if raw_api_response is None:
                raw_api_response = kwargs.get("msg")
            if raw_api_response is not None:
                logger.info(f"{LOG_PREFIX}Raw API response: {raw_api_response}")


def get_node_count_in_workflow(class_type, prompt=None):
    """
    Count nodes of the given class in the current workflow.
    """
    try:
        if prompt is None:
            from server import PromptServer
            prompt = PromptServer.instance.prompt
        
        if not prompt:
            return 0
        
        count = 0
        for key, value in prompt.items():
            if value.get("class_type") == class_type:
                count += 1
        return count
    except Exception:
        return 0


def format_api_error(e):
    """
    Format an API error.
    Extracts the error code and returns the matching readable description.
    """
    error_map = MESSAGES.get("api_errors", {})

    # SDK errors and task error objects carry the code (and request ID) as
    # attributes; plain strings and other exceptions are parsed from the text.
    typed_code = typed_msg = None
    if isinstance(e, dict):
        typed_code, typed_msg = e.get("code"), e.get("message")
    elif not isinstance(e, (str, bytes)):
        typed_code = getattr(e, "code", None)
        if not isinstance(e, BaseException):
            typed_msg = getattr(e, "message", None)
    if not isinstance(typed_code, str) or not typed_code:
        typed_code = None
    request_id = getattr(e, "request_id", None) if isinstance(e, BaseException) else None
    request_suffix = f" Request ID: {request_id}." if request_id else ""

    err_code = typed_code
    err_msg = str(typed_msg) if typed_msg else str(e)
    detected_code = None

    code_match = re.search(r"'code':\s*'([^']+)'", err_msg)
    if code_match and not err_code:
        err_code = code_match.group(1)

    msg_match = re.search(r"'message':\s*'([^']+)'", err_msg)
    if msg_match:
        extracted_msg = msg_match.group(1)
        if len(extracted_msg) > 0:
            err_msg = extracted_msg

    for keyword, mapped_code in ERROR_TEXT_MATCH_RULES.items():
        if keyword.lower() in err_msg.lower():
            detected_code = mapped_code
            break

    def _known(code):
        return bool(code) and (code in error_map or any(str(code).startswith(key) for key in error_map))

    # The API's own code wins; a text rule only adds detail to that same code
    # (InvalidParameter -> InvalidParameter.TaskTypeConstraint) or fills in when
    # the code is missing or unknown.
    if detected_code and err_code and str(detected_code).startswith(str(err_code)):
        final_code = detected_code
    elif _known(err_code):
        final_code = err_code
    else:
        final_code = detected_code or err_code

    if final_code:
        final_code = str(final_code)
        matched_msg = None

        if final_code in error_map:
            matched_msg = error_map[final_code]

        if not matched_msg:
            for key in error_map:
                if final_code.startswith(key):
                    matched_msg = error_map[key]
                    break

        if matched_msg:
            if "%s" in matched_msg:
                def _extract_account_model(text: str):
                    if not text:
                        return None, None
                    m = re.search(
                        r"account\s*\[([^\]]+)\].*?\[([^\]]+)\]\s*model",
                        text,
                        flags=re.IGNORECASE | re.DOTALL,
                    )
                    if m:
                        return m.group(1).strip(), m.group(2).strip()
                    m = re.search(
                        r"your\s+account\s+([^\s]+).*?model\s+([^\s\.\]]+)",
                        text,
                        flags=re.IGNORECASE | re.DOTALL,
                    )
                    if m:
                        return m.group(1).strip(), m.group(2).strip()
                    return None, None

                account, model = _extract_account_model(err_msg)
                if account and model:
                    matched_msg = matched_msg % (account, model)
            return f"{LOG_PREFIX}{matched_msg} (Code: {final_code}){request_suffix}"

    return f"{LOG_PREFIX}Error: {err_msg}{request_suffix}"


def load_api_keys():
    """
    Load the API key file (api_keys.json).
    """
    API_KEY_STORE.load()


def save_api_key(name, key, access_key="", secret_key=""):
    """
    Save a new API key (and optional IAM AK/SK) to api_keys.json.
    """
    if API_KEY_STORE.upsert(name, key, access_key, secret_key):
        logger.info(f"Saved API Key: {name}")


def validate_api_key(api_key: str, base_url: str) -> bool:
    """
    Check that the API key is accepted by the region's endpoint.
    """
    try:
        url = base_url
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 401:
            return False
            
        return True
        
    except Exception as e:
        logger.error(f"API Key validation error: {e}")
        return False


def _normalize_expire_seconds(expire_seconds):
    expire_seconds = int(expire_seconds if expire_seconds is not None else 604800)
    return max(86400, min(expire_seconds, 2592000))


def _normalize_cache_key(cache_key):
    if isinstance(cache_key, str):
        try:
            cache_key = json.loads(cache_key)
        except Exception:
            return None
    if isinstance(cache_key, list):
        cache_key = tuple(cache_key)
    if not isinstance(cache_key, tuple):
        return None
    if len(cache_key) == 2:
        file_path, fps = cache_key
        expire_seconds = 604800
    elif len(cache_key) == 3:
        file_path, fps, expire_seconds = cache_key
    else:
        return None
    if not isinstance(file_path, str) or not file_path:
        return None
    normalized_fps = float(fps) if fps is not None else None
    normalized_expire_seconds = _normalize_expire_seconds(expire_seconds)
    return (file_path, normalized_fps, normalized_expire_seconds)


def _serialize_cache_key(cache_key):
    return json.dumps(list(cache_key), ensure_ascii=False)


def _compute_file_sha256(file_path):
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if chunk:
                hasher.update(chunk)
    return hasher.hexdigest()


class UploadCacheStore:
    def __init__(self, cache_file):
        self.cache_file = cache_file
        self._lock = threading.RLock()
        self._data = {}

    def load(self):
        loaded_data = {}
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                if isinstance(cache_data, dict):
                    now_ts = int(time.time())
                    for raw_key, entry in cache_data.items():
                        cache_key = _normalize_cache_key(raw_key)
                        if cache_key is None or not isinstance(entry, dict):
                            continue
                        file_id = entry.get("file_id")
                        expire_at = int(entry.get("expire_at", 0) or 0)
                        if file_id and expire_at > now_ts:
                            loaded_data[cache_key] = {
                                "file_id": file_id,
                                "expire_at": expire_at
                            }
            except Exception as e:
                logger.error(f"Failed to load upload cache: {e}")
        with self._lock:
            self._data = loaded_data

    def save(self):
        now_ts = int(time.time())
        serializable_data = {}
        with self._lock:
            for raw_key, entry in self._data.items():
                cache_key = _normalize_cache_key(raw_key)
                if cache_key is None or not isinstance(entry, dict):
                    continue
                file_id = entry.get("file_id")
                expire_at = int(entry.get("expire_at", 0) or 0)
                if file_id and expire_at > now_ts:
                    serializable_data[_serialize_cache_key(cache_key)] = {
                        "file_id": file_id,
                        "expire_at": expire_at
                    }
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(serializable_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save upload cache: {e}")

    def get(self, cache_key):
        with self._lock:
            return self._data.get(cache_key)

    def set(self, cache_key, entry):
        with self._lock:
            self._data[cache_key] = entry

    def pop(self, cache_key, default=None):
        with self._lock:
            return self._data.pop(cache_key, default)

    def contains(self, cache_key):
        with self._lock:
            return cache_key in self._data

    def keys(self):
        with self._lock:
            return list(self._data.keys())


UPLOAD_CACHE_STORE = UploadCacheStore(FILES_UPLOAD_CACHE_FILE)


def load_files_upload_cache():
    UPLOAD_CACHE_STORE.load()


def save_files_upload_cache():
    UPLOAD_CACHE_STORE.save()


load_files_upload_cache()


async def upload_file_to_ark(client, file_path, fps=None, expire_seconds=604800, return_meta=False, model=None):
    """
    Upload a file with client.ark.files.create (cached by content). For videos
    (fps set), `model` selects the frame-sampling strategy of that model; without
    it ModelArk uses the strategy of models older than seed-1-8.
    """
    expire_seconds = _normalize_expire_seconds(expire_seconds)
    if fps is None:
        model = None
        try:
            file_identity = f"sha256:{await asyncio.to_thread(_compute_file_sha256, file_path)}"
        except Exception:
            file_identity = file_path
    else:
        file_identity = file_path
    if model:
        # Preprocessing depends on the model, so the upload is cached per model.
        file_identity = f"{file_identity}|model={model}"
    cache_key = (file_identity, float(fps) if fps is not None else None, expire_seconds)
    cache_fps = float(fps) if fps is not None else None
    now_ts = int(time.time())
    expire_at = now_ts + expire_seconds

    cached_entry = UPLOAD_CACHE_STORE.get(cache_key)
    legacy_path_key = None
    matched_cache_key = cache_key
    if cached_entry is None and fps is None and file_identity != file_path:
        legacy_path_key = (file_path, None, expire_seconds)
        cached_entry = UPLOAD_CACHE_STORE.get(legacy_path_key)
        if cached_entry is not None:
            matched_cache_key = legacy_path_key
    if isinstance(cached_entry, dict):
        cached_file_id = cached_entry.get("file_id")
        cached_expire_at = int(cached_entry.get("expire_at", 0) or 0)
        if cached_file_id:
            try:
                remote_file_info = await asyncio.to_thread(
                    client.ark.files.retrieve,
                    file_id=cached_file_id
                )
                remote_status = getattr(remote_file_info, "status", "unknown")
                remote_expire_at = int(getattr(remote_file_info, "expire_at", 0) or 0)
                if remote_status == "active" and remote_expire_at > now_ts:
                    cached_entry["expire_at"] = remote_expire_at
                    if legacy_path_key is not None and UPLOAD_CACHE_STORE.contains(legacy_path_key):
                        UPLOAD_CACHE_STORE.pop(legacy_path_key, None)
                        UPLOAD_CACHE_STORE.set(cache_key, cached_entry)
                    save_files_upload_cache()
                    log_msg("visual_found_file", path=file_path)
                    if return_meta:
                        return {
                            "file_id": cached_file_id,
                            "expire_at": remote_expire_at
                        }
                    return cached_file_id
            except Exception:
                if cached_expire_at > now_ts:
                    if legacy_path_key is not None and UPLOAD_CACHE_STORE.contains(legacy_path_key):
                        UPLOAD_CACHE_STORE.pop(legacy_path_key, None)
                        UPLOAD_CACHE_STORE.set(cache_key, cached_entry)
                        save_files_upload_cache()
                    log_msg("visual_found_file", path=file_path)
                    if return_meta:
                        return {
                            "file_id": cached_file_id,
                            "expire_at": cached_expire_at
                        }
                    return cached_file_id
        UPLOAD_CACHE_STORE.pop(matched_cache_key, None)
        save_files_upload_cache()
    elif cached_entry is not None:
        UPLOAD_CACHE_STORE.pop(cache_key, None)
        save_files_upload_cache()

    identity_candidates = {file_identity}
    if fps is None and file_identity != file_path:
        identity_candidates.add(file_path)
    stale_keys = []
    for existing_key in UPLOAD_CACHE_STORE.keys():
        normalized_existing_key = _normalize_cache_key(existing_key)
        if normalized_existing_key is None:
            continue
        existing_identity, existing_fps, existing_expire_seconds = normalized_existing_key
        if (
            existing_identity in identity_candidates
            and existing_fps == cache_fps
            and existing_expire_seconds != expire_seconds
        ):
            stale_keys.append(existing_key)

    if stale_keys and hasattr(client.ark, "files"):
        cache_changed = False
        for stale_key in stale_keys:
            stale_entry = UPLOAD_CACHE_STORE.get(stale_key)
            if isinstance(stale_entry, dict):
                stale_file_id = stale_entry.get("file_id")
                if stale_file_id:
                    try:
                        await asyncio.to_thread(
                            client.ark.files.delete,
                            file_id=stale_file_id
                        )
                    except Exception as e:
                        logger.error(f"Delete stale file failed for {stale_file_id}: {e}")
            UPLOAD_CACHE_STORE.pop(stale_key, None)
            cache_changed = True
        if cache_changed:
            save_files_upload_cache()
        
    try:
        log_msg("visual_uploading", path=file_path)
        if not hasattr(client.ark, "files"):
            raise BytePlusException(get_text("err_files_api_missing"))
        with open(file_path, "rb") as f:
            upload_kwargs = {
                "file": f,
                "purpose": "user_data",
                # BytePlus SDK name (the returned file object uses expire_at)
                "expires_at": expire_at,
            }
            if fps is not None:
                video_config = {"fps": float(fps)}
                if model:
                    video_config["model"] = model
                upload_kwargs["preprocess_configs"] = {"video": video_config}
            file_obj = await asyncio.to_thread(client.ark.files.create, **upload_kwargs)

        file_id = getattr(file_obj, "id", None)
        if not file_id:
            raise BytePlusException(get_text("err_file_no_id"))
        log_msg("visual_uploaded", id=file_id, status=getattr(file_obj, "status", "unknown"))

        try:
            await wait_for_file_active(client, file_id)
        except BytePlusException:
            # Failed or timed out: don't leave an unusable file in Ark Files.
            await _delete_file_quietly(client, file_id)
            raise

        UPLOAD_CACHE_STORE.set(cache_key, {"file_id": file_id, "expire_at": expire_at})
        save_files_upload_cache()
        if return_meta:
            return {"file_id": file_id, "expire_at": expire_at}
        return file_id
    except BytePlusException:
        raise
    except Exception as e:
        logger.error(f"Upload failed for {file_path}: {e}")
        raise BytePlusException(
            get_text("err_file_upload_failed", e=format_api_error(e).replace(LOG_PREFIX, "", 1))
        )


async def _delete_file_quietly(client, file_id):
    try:
        await asyncio.to_thread(client.ark.files.delete, file_id=file_id)
    except Exception as e:
        logger.warning(f"Could not delete file {file_id}: {e}")


FILE_ACTIVE_POLL_SECONDS = 1
FILE_ACTIVE_MAX_WAIT_SECONDS = 600
FILE_ACTIVE_MAX_RETRIEVE_ERRORS = 5


async def wait_for_file_active(client, file_id, max_wait_seconds=FILE_ACTIVE_MAX_WAIT_SECONDS):
    """
    Poll the Files API until the file is "active". Raises on "failed", on a
    timeout, after repeated retrieve errors, or when the user interrupts.
    """
    log_msg("visual_wait_active", id=file_id)
    deadline = time.monotonic() + max_wait_seconds
    retrieve_errors = 0

    while True:
        comfy.model_management.throw_exception_if_processing_interrupted()
        try:
            file_info = await asyncio.to_thread(client.ark.files.retrieve, file_id=file_id)
        except Exception as e:
            retrieve_errors += 1
            logger.error(f"Error checking file status: {e}")
            if retrieve_errors >= FILE_ACTIVE_MAX_RETRIEVE_ERRORS:
                raise BytePlusException(get_text("err_file_status_check", id=file_id, e=format_api_error(e)))
        else:
            retrieve_errors = 0
            status = getattr(file_info, "status", "unknown")
            log_msg("visual_file_status", id=file_id, status=status)
            if status == "active":
                return True
            if status == "failed":
                error = getattr(file_info, "error", None)
                reason = getattr(error, "message", None) or error or "unknown error"
                raise BytePlusException(get_text("err_file_processing_failed", id=file_id, reason=reason))

        if time.monotonic() >= deadline:
            raise BytePlusException(
                get_text("err_file_processing_timeout", id=file_id, seconds=max_wait_seconds)
            )
        await asyncio.sleep(FILE_ACTIVE_POLL_SECONDS)


def _tensor2images(tensor: torch.Tensor) -> list:
    """
    Convert a PyTorch tensor to a list of PIL images.
    """
    np_imgs = numpy.clip(tensor.cpu().numpy() * 255.0, 0, 255.0).astype(numpy.uint8)
    return [PIL.Image.fromarray(np_img) for np_img in np_imgs]


def _image_to_base64(image: torch.Tensor) -> str:
    """
    Encode a single image tensor as a Base64 JPEG string.
    """
    if image is None:
        return None
    with io.BytesIO() as bytes_io:
        _tensor2images(image)[0].save(bytes_io, format="JPEG")
        data_bytes = bytes_io.getvalue()
    return base64.b64encode(data_bytes).decode("utf-8")


def create_white_image_tensor(width=1024, height=1024):
    """
    Create a plain white image tensor of the given size.
    Shape: [1, height, width, 3]
    """
    return torch.ones((1, height, width, 3), dtype=torch.float32)


def safe_cat_tensors(tensors, dim=0):
    """
    Concatenate tensors, resizing any that differ from the first.
    Adaptive sizes can return images of different dimensions.
    """
    if not tensors:
        return None

    if not isinstance(tensors, list):
        return tensors

    if len(tensors) == 0:
        return None

    target_tensor = tensors[0]
    target_h, target_w = target_tensor.shape[1], target_tensor.shape[2]

    processed_tensors = []
    for t in tensors:
        if t.shape[1] != target_h or t.shape[2] != target_w:
            t_permuted = t.permute(0, 3, 1, 2)
            t_resized = F.interpolate(
                t_permuted, size=(target_h, target_w), mode="bilinear", align_corners=False
            )
            t_final = t_resized.permute(0, 2, 3, 1)
            processed_tensors.append(t_final)
        else:
            processed_tensors.append(t)

    return torch.cat(processed_tensors, dim=dim)


def create_white_video(width=1024, height=1024, fps=24):
    """
    One-frame white VIDEO (placeholder output when failures are ignored).
    Built with ComfyUI's own video types, so no OpenCV is needed.
    """
    try:
        from fractions import Fraction
        from comfy_api.latest import InputImpl, Types

        frames = torch.ones((1, height, width, 3), dtype=torch.float32)
        return InputImpl.VideoFromComponents(
            Types.VideoComponents(images=frames, frame_rate=Fraction(fps))
        )
    except Exception as e:
        log_msg("err_create_dummy_video", e=e)
        return None


def probe_video_file(path):
    """
    Read fps, frame count, duration and codecs of a local video with PyAV
    (shipped with ComfyUI). Returns {} when the file cannot be read.
    """
    try:
        import av

        with av.open(path) as container:
            video_stream = next((s for s in container.streams if s.type == "video"), None)
            audio_stream = next((s for s in container.streams if s.type == "audio"), None)
            info = {}
            if video_stream is not None:
                if video_stream.average_rate:
                    info["fps"] = float(video_stream.average_rate)
                if video_stream.frames:
                    info["frame_count"] = int(video_stream.frames)
                info["video_codec"] = video_stream.codec_context.name
            if audio_stream is not None:
                info["audio_codec"] = audio_stream.codec_context.name
            if container.duration:
                info["duration"] = float(container.duration) / av.time_base
            return info
    except Exception:
        return {}


def video_source_size_bytes(video):
    """
    Size in bytes of a VIDEO's own file (path or in-memory buffer), without
    encoding anything. None when it has no source or is trimmed, since the
    uploaded file is then a re-encode of unknown size.
    """
    try:
        start, duration = video.get_active_trim_window()
    except Exception:
        start, duration = 0, 0
    if start or duration:
        return None
    try:
        source = video.get_stream_source()
    except Exception:
        return None
    if isinstance(source, str):
        try:
            return os.path.getsize(source)
        except OSError:
            return None
    if hasattr(source, "getbuffer"):
        return source.getbuffer().nbytes
    return None


def extract_last_frame_tensor(path):
    """
    Decode the last frame of a local video as an IMAGE tensor [1, H, W, 3],
    or None. Seeks close to the end first so long videos are not fully decoded.
    """
    try:
        import av

        with av.open(path) as container:
            stream = container.streams.video[0]
            if container.duration and container.duration > 2 * av.time_base:
                container.seek(int(container.duration - 2 * av.time_base), backward=True, any_frame=False)
            last = None
            for frame in container.decode(stream):
                last = frame
            if last is None:
                return None
            image = last.to_ndarray(format="rgb24").astype(numpy.float32) / 255.0
            return torch.from_numpy(image)[None,]
    except Exception as e:
        logger.warning(f"Failed to extract the last frame locally: {e}")
        return None


class BytePlusException(Exception):
    """
    Plugin exception whose traceback is suppressed in the console
    (byteplus_suppress_traceback = True); only the message is shown.
    """
    def __init__(self, message):
        super().__init__(message)
        self.byteplus_suppress_traceback = True


async def wait_interruptible(awaitable, poll_seconds=0.5):
    """Await while honouring ComfyUI interrupts (cancels the request, then re-raises)."""
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll_seconds)
            if done:
                return task.result()
            comfy.model_management.throw_exception_if_processing_interrupted()
    except BaseException:
        task.cancel()
        raise


async def sleep_interruptible(seconds):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        comfy.model_management.throw_exception_if_processing_interrupted()
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(0.5, remaining))


async def gather_cancelling(awaitables, return_exceptions=False):
    """
    asyncio.gather that never leaves siblings running. ComfyUI clears its
    interrupt flag when the first request raises InterruptProcessingException,
    so the other requests would not see it and the node would wait for all of
    them (paid work included). Here an interrupt, or any error unless
    return_exceptions is set, cancels the rest before it is re-raised.
    With return_exceptions, other errors are returned in place of results.
    """
    tasks = [asyncio.ensure_future(awaitable) for awaitable in awaitables]
    try:
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                error = None if task.cancelled() else task.exception()
                if error is not None and (
                    not return_exceptions or isinstance(error, comfy.model_management.InterruptProcessingException)
                ):
                    raise error
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return [task.exception() or task.result() if return_exceptions else task.result() for task in tasks]


def plain_text(key, **kwargs):
    """A message without the console prefix, for use inside another message or on a node."""
    text = get_text(key, **kwargs)
    return text[len(LOG_PREFIX):] if text.startswith(LOG_PREFIX) else text


def send_node_text(node_id, text, ps_instance=None):
    """Progress text under the node (best effort); the console prefix is dropped."""
    if not node_id:
        return
    try:
        if ps_instance is None:
            from server import PromptServer

            ps_instance = PromptServer.instance
        if ps_instance:
            ps_instance.send_progress_text(text.replace(LOG_PREFIX, "", 1), node_id)
    except Exception:
        pass


async def upload_bytes_to_comfy_storage(node_cls, data, filename, mime_type, cache, *, cache_key, ttl_seconds,
                                        max_entries, unavailable_key, failed_key, done_key=None, **message_kwargs):
    """
    Comfy.org storage URL for media an API only takes as a public link,
    reused from `cache` for the same cache_key within ttl_seconds. Needs a
    Comfy.org login; the helper is internal to ComfyUI and missing with
    --disable-api-nodes. Message keys get message_kwargs plus e.
    """
    cached = cache.get(cache_key)
    if cached and time.time() - cached[1] < ttl_seconds:
        return cached[0]
    try:
        from comfy_api_nodes.util import upload_file_to_comfyapi
    except Exception as e:
        raise BytePlusException(get_text(unavailable_key, e=e, **message_kwargs))
    try:
        url = await upload_file_to_comfyapi(node_cls, io.BytesIO(data), filename, mime_type, wait_label=None)
    except comfy.model_management.InterruptProcessingException:
        raise
    except Exception as e:
        raise BytePlusException(get_text(failed_key, e=e, **message_kwargs))
    cache.pop(cache_key, None)
    cache[cache_key] = (url, time.time())
    while len(cache) > max_entries:
        cache.pop(next(iter(cache)))
    if done_key:
        log_msg(done_key, **message_kwargs)
    return url


class BytePlusClients:
    """
    Wraps the Ark client together with its API key, region and (optional)
    asset-library IAM credentials. Never serialized into outputs.
    billed_ark: the same client without automatic retries, for calls that
    start paid work (see call_billed).
    """
    def __init__(self, ark_client, api_key=None, region=DEFAULT_REGION, asset_credentials=None, billed_ark=None):
        self.ark = ark_client
        self.billed_ark = billed_ark
        self.api_key = api_key
        self.region = region
        self.asset_credentials = asset_credentials

    def check_quota(self, model: str, estimated_cost: int):
        if not self.api_key:
            return
        from .quota import QuotaManager
        QuotaManager.instance().check_quota(self.api_key, model, estimated_cost)

    def update_usage(self, model: str, actual_cost: int):
        if not self.api_key:
            return
        from .quota import QuotaManager
        QuotaManager.instance().update_usage(self.api_key, model, actual_cost)


# The Ark SDK retries a failed request up to twice: on timeouts, 408, 409, 429
# and 5xx. For a call that starts paid work (task creation, image generation,
# LLM responses) a retry after a timeout or server error can create and bill
# the same work twice: the first request may have gone through, and ModelArk
# has no idempotency key (ComfyUI core sends Idempotency-Key to its proxy for
# the same reason). Those calls use billed_ark (no SDK retries) via
# call_billed, which only retries rate limits: a 429 means nothing started.
BILLED_RATE_LIMIT_RETRIES = 2

try:
    from byteplussdkarkruntime._exceptions import ArkRateLimitError
except Exception:  # SDK layout changed: no 429 retry, never a duplicate
    class ArkRateLimitError(Exception):
        pass


def billed_ark(client):
    """The Ark client for calls that start paid work: no automatic retries."""
    return getattr(client, "billed_ark", None) or client.ark


def call_billed(create, /, **kwargs):
    """
    Run a call that starts paid work (blocking; call it in a worker thread).
    Only rate limits are retried, after 1 s and 2 s.
    """
    for attempt in range(BILLED_RATE_LIMIT_RETRIES + 1):
        try:
            return create(**kwargs)
        except ArkRateLimitError:
            if attempt >= BILLED_RATE_LIMIT_RETRIES:
                raise
            comfy.model_management.throw_exception_if_processing_interrupted()
            time.sleep(2 ** attempt)


API_KEY_SAVED_EVENT = "byteplus.api_key_saved"


def api_key_fingerprint(api_key):
    """Short one-way fingerprint so the frontend can find the node holding this key."""
    return hashlib.sha256(api_key.strip().encode("utf-8")).hexdigest()[:16]


def _notify_api_key_saved(node_id, key_name, api_key, store="modelark"):
    """
    Tell the frontend a Custom key was saved, so the node switches to the saved
    name and clears the raw key. Otherwise the key stays in the workflow and in
    the prompt metadata embedded in every saved image or video.

    Sent only to the browser client that queued the prompt (not broadcast), with
    a fingerprint of the key: the frontend switches only API Client nodes whose
    pasted key matches, wherever they are (other tabs, subgraphs), and never
    receives the key itself. ``store`` tells ModelArk keys ("modelark") from
    Seed Speech keys ("speech"), which live in separate files and nodes.
    """
    if not node_id:
        return
    try:
        from server import PromptServer

        server = PromptServer.instance
        server.send_sync(
            API_KEY_SAVED_EVENT,
            {
                "node": str(node_id),
                "key_name": key_name,
                "key_fingerprint": api_key_fingerprint(api_key),
                "store": store,
                "prompt_id": getattr(server, "last_prompt_id", None),
            },
            getattr(server, "client_id", None),
        )
    except Exception as e:
        logger.warning(f"Could not notify the frontend that the API key was saved: {e}")


class BytePlusAPIClient(comfy_io.ComfyNode):
    """
    BytePlus ModelArk API client node.
    Loads the API key and creates the Ark client for the selected region.
    """
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        load_api_keys()
        key_names = API_KEY_STORE.get_key_names()
        key_names.append("Custom")

        return comfy_io.Schema(
            node_id="BytePlusAPIClient",
            display_name="BytePlus API Client",
            category=GLOBAL_CATEGORY,
            inputs=[
                comfy_io.String.Input("new_api_key", default=""),
                comfy_io.String.Input("new_key_name", default=""),
                comfy_io.Combo.Input("key_name", options=key_names),
                comfy_io.Combo.Input(
                    "region",
                    options=list(REGION_BASE_URLS.keys()),
                    default=DEFAULT_REGION,
                    tooltip="ModelArk region. API keys and model activation are per region.",
                ),
                comfy_io.String.Input(
                    "new_access_key",
                    default="",
                    optional=True,
                    tooltip=(
                        "Optional IAM access key (AK), only for the asset library nodes "
                        "(Create Image / Video / Audio Asset, Asset Library, and asset_N references). "
                        "Use it with new_secret_key while key_name is Custom: it is saved with the key "
                        "under new_key_name and then cleared from this node. Use an IAM sub-user "
                        "whose policy only allows the asset library."
                    ),
                ),
                comfy_io.String.Input(
                    "new_secret_key",
                    default="",
                    optional=True,
                    tooltip="Optional IAM secret key (SK) that goes with new_access_key. Cleared from this node after it is saved.",
                ),
            ],
            outputs=[BytePlusClientType.Output(display_name="client")],
            hidden=[comfy_io.Hidden.unique_id],
        )

    @classmethod
    def execute(
        cls, key_name, new_api_key="", new_key_name="", region=DEFAULT_REGION,
        new_access_key="", new_secret_key="",
    ) -> comfy_io.NodeOutput:
        api_key = None
        asset_credentials = None
        base_url = REGION_BASE_URLS.get(region, REGION_BASE_URLS[DEFAULT_REGION])

        if key_name == "Custom":
            if not new_api_key or not new_api_key.strip():
                raise BytePlusException(get_text("err_new_key_empty"))
            
            api_key = new_api_key.strip()
            access_key = (new_access_key or "").strip()
            secret_key = (new_secret_key or "").strip()
            if bool(access_key) != bool(secret_key):
                raise BytePlusException(get_text("err_new_asset_credentials_incomplete"))

            if not validate_api_key(api_key, base_url):
                raise BytePlusException(get_text("err_new_key_invalid"))

            if access_key:
                # Usable in this run even when the key is not saved.
                asset_credentials = {"access_key": access_key, "secret_key": secret_key, "session_token": ""}

            if new_key_name and new_key_name.strip():
                save_api_key(new_key_name.strip(), api_key, access_key, secret_key)
                print(get_text("info_new_key_saved", name=new_key_name.strip()))
                if access_key:
                    print(get_text("info_new_asset_credentials_saved", name=new_key_name.strip()))
                _notify_api_key_saved(cls.hidden.unique_id, new_key_name.strip(), api_key)

        else:
            api_key = API_KEY_STORE.find_api_key(key_name)
            asset_credentials = API_KEY_STORE.find_asset_credentials(key_name)

        if not api_key:
            log_msg("api_key_not_found", key_name=key_name)
            raise BytePlusException(get_text("popup_key_valid_err").format(key=key_name))

        ark_client = Ark(api_key=api_key, base_url=base_url)
        billed_client = Ark(api_key=api_key, base_url=base_url, max_retries=0)

        return comfy_io.NodeOutput(
            BytePlusClients(ark_client, api_key, region, asset_credentials, billed_ark=billed_client)
        )
