import asyncio
import contextlib
import io
import ipaddress
import json
import math
import os
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import aiohttp
import comfy.model_management
import folder_paths
import numpy
import PIL.Image
import torch
from comfy_api.input_impl import VideoFromFile
from comfy_execution.graph_utils import ExecutionBlocker

from .audio_utils import audio_to_wav_bytes
from .constants import LAS_DOWNLOAD_STALL_SECONDS
from .core_style import PublicOnlyResolver
from .nodes_shared import (
    BytePlusException,
    extract_last_frame_tensor,
    get_text,
    upload_bytes_to_comfy_storage,
    wait_interruptible,
)
from .nodes_video import upload_videos_to_comfy_storage_cached
from .utils_download import describe_download_error, download_to_file

CAPTION_MAX_BYTES = 32 * 1024 * 1024
IMAGE_MAX_BYTES = 32 * 1024 * 1024


def invalid(detail):
    raise BytePlusException(get_text("err_las_input", detail=detail))


def check_source(media, reference, name, required=True):
    if reference is not None and not isinstance(reference, str):
        invalid(f"{name}_url must be text.")
    reference = (reference or "").strip()
    if media is not None and reference:
        invalid(f"Connect {name} or set {name}_url, not both.")
    if required and media is None and not reference:
        invalid(f"Connect {name} or set {name}_url.")
    return reference


def check_download_url(url):
    if not isinstance(url, str) or any(char.isspace() or ord(char) < 32 for char in url):
        invalid("Artifact URL must be a public HTTP(S) URL.")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        invalid("Artifact URL is malformed.")
    if parsed.scheme not in ("http", "https") or not host or parsed.username is not None or parsed.password is not None:
        invalid("Artifact URL must be HTTP(S) without embedded credentials.")
    if port is not None and not 1 <= port <= 65535:
        invalid("Artifact URL port is invalid.")
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local")):
        invalid("Artifact host must be public.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        invalid("Artifact address must be public.")
    return url


@asynccontextmanager
async def safe_get(session, url, **kwargs):
    current = check_download_url(url)
    for redirect in range(6):
        async with session.get(current, allow_redirects=False, **kwargs) as response:
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location or redirect == 5:
                    invalid("Artifact redirect limit exceeded or missing Location.")
                current = check_download_url(urljoin(current, location))
                continue
            response.raise_for_status()
            yield response
            return


class SafeDownloadSession:
    def __init__(self, session):
        self.session = session

    def get(self, url, **kwargs):
        return safe_get(self.session, url, **kwargs)


def split_audio(audio, single=False):
    if not isinstance(audio, dict):
        invalid("Audio must contain waveform and sample_rate.")
    waveform, sample_rate = audio.get("waveform"), audio.get("sample_rate")
    if (
        not isinstance(waveform, torch.Tensor)
        or waveform.ndim != 3
        or any(size < 1 for size in waveform.shape)
        or not isinstance(sample_rate, int)
        or isinstance(sample_rate, bool)
        or sample_rate <= 0
        or not torch.isfinite(waveform).all()
    ):
        invalid("Audio must be a finite nonempty [batch, channels, samples] tensor with a positive sample rate.")
    if single and waveform.shape[0] != 1:
        invalid("External dubbed audio requires exactly one clip, not an AUDIO batch.")
    return [{"waveform": clip[None], "sample_rate": sample_rate} for clip in waveform]


async def upload_video(node_cls, video):
    urls = await wait_interruptible(
        upload_videos_to_comfy_storage_cached(
            node_cls, [video], unavailable_key="las_video_upload_unavailable", failed_key="las_video_upload_failed"
        )
    )
    return urls[0]


def image_png(image):
    if not isinstance(image, torch.Tensor) or image.ndim != 3 or image.shape[-1] not in (3, 4):
        invalid("Reference images must be RGB or RGBA images.")
    if min(image.shape) < 1 or not torch.isfinite(image).all():
        invalid("Reference images must be finite and nonempty.")
    array = (image.detach().cpu().clamp(0, 1).numpy() * 255).round().astype(numpy.uint8)
    with io.BytesIO() as buffer:
        PIL.Image.fromarray(array).save(buffer, format="PNG")
        return buffer.getvalue()


async def upload_images(node_cls, images):
    if images is None:
        return []
    if not isinstance(images, torch.Tensor) or images.ndim != 4 or images.shape[0] < 1:
        invalid("reference_images must be a nonempty IMAGE batch.")
    urls = []
    for image in images:
        data = await wait_interruptible(asyncio.to_thread(image_png, image))
        urls.append(
            await upload_bytes_to_comfy_storage(
                node_cls,
                data,
                "reference.png",
                "image/png",
                cache_kind="las_image",
                unavailable_key="las_upload_unavailable",
                failed_key="las_upload_failed",
                kind="image",
            )
        )
    return urls


async def upload_audio(node_cls, audio, single=False):
    urls = []
    for clip in split_audio(audio, single=single):
        data = await wait_interruptible(asyncio.to_thread(audio_to_wav_bytes, clip))
        urls.append(
            await upload_bytes_to_comfy_storage(
                node_cls,
                data,
                "reference.wav",
                "audio/wav",
                cache_kind="las_audio",
                unavailable_key="las_upload_unavailable",
                failed_key="las_upload_failed",
                kind="audio",
            )
        )
    return urls


def video_metadata(video):
    import av

    source = video.get_stream_source()
    width, height = video.get_dimensions()
    metadata = {
        "duration": float(video.get_duration()),
        "fps": float(video.get_frame_rate()),
        "width": width,
        "height": height,
        "size": None,
        "has_audio": None,
    }
    if source is not None:
        if isinstance(source, str):
            metadata["size"] = os.path.getsize(source)
        elif isinstance(source, io.BytesIO):
            metadata["size"] = source.getbuffer().nbytes
        with av.open(source) as container:
            if not container.streams.video:
                invalid("Source has no video stream.")
            stream = container.streams.video[0]
            metadata["has_audio"] = bool(container.streams.audio)
            if stream.duration is not None and stream.time_base is not None:
                duration = float(stream.duration * stream.time_base)
                window = getattr(video, "get_active_trim_window", None)
                start, length = window() if callable(window) else (0, 0)
                metadata["duration"] = max(0, min(duration - float(start), float(length) or duration))
    else:
        metadata["has_audio"] = video.get_components().audio is not None
    return metadata


def validate_translation_metadata(metadata, lip_sync=False, external_audio=False, restoration=False):
    duration, fps = metadata["duration"], metadata["fps"]
    if not math.isfinite(duration) or not 2 <= duration <= (600 if lip_sync else 14400):
        invalid("Translation video must be 2 seconds to 4 hours, or at most 10 minutes with lip sync.")
    if not math.isfinite(fps) or fps <= 0 or (lip_sync and fps > 30):
        invalid("Video frame rate must be positive and at most 30 FPS with lip sync.")
    if metadata.get("size") is not None and metadata["size"] > 10_000_000_000:
        invalid("Translation video exceeds 10 GB. Persistent URL/TOS sources avoid Comfy.org upload limits.")
    if not external_audio and metadata.get("has_audio") is False:
        invalid("Translation and source-audio lip sync require a source audio track.")
    if restoration:
        width, height = metadata["width"], metadata["height"]
        if duration > 10800 or max(width, height) > 3840 or min(width, height) > 2160:
            invalid("Restoration input is limited to 3 hours and 3840 x 2160.")


def validate_alignment(source, other, label):
    if abs(source["duration"] - other["duration"]) > 1:
        invalid(f"{label} duration must differ from the source by at most one second.")


def decode_audio_file(path):
    import av

    chunks = []
    sample_rate = None
    with av.open(path) as container:
        if not container.streams.audio:
            invalid("Downloaded file has no audio track.")
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(
            format="fltp", layout=stream.codec_context.layout, rate=stream.codec_context.sample_rate
        )
        for frame in container.decode(stream):
            comfy.model_management.throw_exception_if_processing_interrupted()
            for normalized in resampler.resample(frame):
                chunks.append(normalized.to_ndarray())
                sample_rate = normalized.sample_rate
        for normalized in resampler.resample(None):
            chunks.append(normalized.to_ndarray())
            sample_rate = normalized.sample_rate
    if not chunks or not sample_rate:
        invalid("Downloaded audio has no samples.")
    return {"waveform": torch.from_numpy(numpy.concatenate(chunks, axis=1).copy())[None], "sample_rate": sample_rate}


def decode_caption(data):
    if len(data) > CAPTION_MAX_BYTES:
        invalid("Caption exceeds the 32 MiB local safety limit.")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")


async def _bounded_download(session, url, path, limit):
    async with safe_get(session, url) as response:
        size = 0
        with open(path, "wb") as handle:
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                if size > limit:
                    invalid("Artifact exceeds the local download safety limit.")
                handle.write(chunk)


def _artifact_summary(asset):
    return {
        "language": asset.get("language"),
        "type": asset.get("type"),
        "format": asset.get("format"),
        "tos_path": asset.get("tos_path"),
        "download_status": "skipped",
        "local_path": None,
        "text": None,
        "error": None,
    }


async def _collect_asset(asset, task_id):
    url = check_download_url(asset.get("presigned_url"))
    kind = asset.get("type")
    extension = {"translated_caption": ".txt", "translated_audio": ".wav", "last_frame": ".png"}.get(kind, ".mp4")
    directory = os.path.join(folder_paths.get_temp_directory(), "BytePlusLAS")
    os.makedirs(directory, exist_ok=True)
    descriptor, path = tempfile.mkstemp(dir=directory, suffix=extension)
    os.close(descriptor)
    keep = False
    try:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=60, sock_read=LAS_DOWNLOAD_STALL_SECONDS)
        connector = aiohttp.TCPConnector(resolver=PublicOnlyResolver())
        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector, cookie_jar=aiohttp.DummyCookieJar()
        ) as session:
            if kind in ("translated_caption", "last_frame"):
                limit = CAPTION_MAX_BYTES if kind == "translated_caption" else IMAGE_MAX_BYTES
                await wait_interruptible(_bounded_download(session, url, path, limit))
            else:
                await download_to_file(SafeDownloadSession(session), url, path, timeout=timeout)
        summary = _artifact_summary(asset)
        summary["download_status"] = "downloaded"
        if kind == "translated_caption":
            with open(path, "rb") as handle:
                value = decode_caption(handle.read(CAPTION_MAX_BYTES + 1))
            summary["text"] = value
        elif kind == "translated_audio":
            value = await wait_interruptible(asyncio.to_thread(decode_audio_file, path))
        elif kind == "last_frame":
            with PIL.Image.open(path) as image:
                value = torch.from_numpy(numpy.array(image.convert("RGB"), dtype=numpy.float32) / 255)[None]
        else:
            value = VideoFromFile(path)
            await wait_interruptible(asyncio.to_thread(value.get_dimensions))
            summary["local_path"] = path
            keep = True
        return value, summary
    finally:
        if not keep:
            with contextlib.suppress(OSError):
                os.remove(path)


@dataclass
class LASCollection:
    values: tuple
    complete: bool


async def collect_translation(
    task,
    languages=None,
    primary_caption_format=".srt",
    download_audio=True,
    download_captions=True,
    download_repaired_video=True,
    caption_formats=None,
    api_key=None,
    require_audio=False,
    require_repaired_video=False,
):
    from .las_api import redact_las_response

    task_id = task["metadata"]["task_id"]
    data = task.get("data") or {}
    all_assets = []
    for field in ("translated_videos", "translated_captions", "repaired_videos"):
        entries = data.get(field, [])
        if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
            invalid(f"LAS {field} must be an array of artifacts.")
        all_assets.extend(entries)
    returned = list(
        dict.fromkeys(asset.get("language") or "" for asset in all_assets if asset.get("type") == "translated_video")
    )
    languages = list(languages) if languages else (returned or [""])
    manifest = {"task_id": task_id, "requested_languages": languages, "returned_languages": returned, "artifacts": []}
    groups = {}
    for asset in all_assets:
        identity = (
            asset.get("language") or "",
            asset.get("type"),
            asset.get("format") if asset.get("type") == "translated_caption" else None,
        )
        groups.setdefault(identity, []).append(asset)
    videos, audios, captions = [], [], []
    repaired = ExecutionBlocker(None)
    complete = True
    owned_paths = []

    async def collect(identity, enabled=True, required=False):
        nonlocal complete
        entries = groups.pop(identity, [])
        if not enabled:
            manifest["artifacts"].extend(_artifact_summary(asset) for asset in entries)
            return ExecutionBlocker(None)
        if not entries:
            if required:
                complete = False
                manifest["artifacts"].append(
                    {
                        **_artifact_summary({"language": identity[0], "type": identity[1], "format": identity[2]}),
                        "download_status": "missing",
                        "error": "Requested artifact is missing.",
                    }
                )
            return ExecutionBlocker(
                get_text("err_las_download", task_id=task_id, reason="missing artifact") if required else None
            )
        if len(entries) != 1:
            complete = False
            manifest["artifacts"].extend(
                {**_artifact_summary(asset), "download_status": "failed", "error": "Duplicate artifact identity."}
                for asset in entries
            )
            return ExecutionBlocker(get_text("err_las_download", task_id=task_id, reason="duplicate artifact"))
        asset = entries[0]
        try:
            value, summary = await _collect_asset(asset, task_id)
            manifest["artifacts"].append(summary)
            if summary.get("local_path"):
                owned_paths.append(summary["local_path"])
            return value
        except Exception as error:
            complete = False
            message = get_text("err_las_download", task_id=task_id, reason=describe_download_error(error))
            manifest["artifacts"].append({**_artifact_summary(asset), "download_status": "failed", "error": message})
            return ExecutionBlocker(message)

    try:
        for language in languages:
            videos.append(await collect((language, "translated_video", None), required=True))
            audios.append(
                await collect(
                    (language, "translated_audio", None),
                    enabled=download_audio,
                    required=require_audio and download_audio,
                )
            )
            formats = caption_formats or list(
                dict.fromkeys(
                    identity[2]
                    for identity in groups
                    if identity[0] == language and identity[1] == "translated_caption"
                )
            )
            primary = ExecutionBlocker(None)
            for caption_format in formats:
                text = await collect(
                    (language, "translated_caption", caption_format),
                    enabled=download_captions,
                    required=bool(caption_formats) and download_captions,
                )
                if caption_format == primary_caption_format:
                    primary = text
            captions.append(primary)
        repaired = await collect(
            ("", "repaired_video", None),
            enabled=download_repaired_video,
            required=require_repaired_video and download_repaired_video,
        )
        for entries in groups.values():
            manifest["artifacts"].extend(
                {**_artifact_summary(asset), "error": "Unmapped artifact; not downloaded."} for asset in entries
            )
        values = (
            videos,
            audios,
            captions,
            languages,
            repaired,
            json.dumps(redact_las_response(manifest, api_key), ensure_ascii=False),
            task_id,
            json.dumps(redact_las_response(task, api_key), ensure_ascii=False),
        )
        return LASCollection(values, complete)
    except BaseException:
        for path in owned_paths:
            with contextlib.suppress(OSError):
                os.remove(path)
        raise


async def collect_genjutsu(task, return_last_frame=False, api_key=None):
    from .las_api import redact_las_response

    task_id = task["metadata"]["task_id"]
    content = (task.get("data") or {}).get("content") or {}
    asset = {"type": "translated_video", "presigned_url": content.get("video_url")}
    video = ExecutionBlocker(None)
    frame = ExecutionBlocker(None)
    complete = True
    path = None
    try:
        try:
            video, summary = await _collect_asset(asset, task_id)
            path = summary.get("local_path")
        except Exception as error:
            complete = False
            video = ExecutionBlocker(
                get_text("err_las_download", task_id=task_id, reason=describe_download_error(error))
            )
        if return_last_frame and path:
            try:
                if content.get("last_frame_url"):
                    frame, _ = await _collect_asset(
                        {"type": "last_frame", "presigned_url": content["last_frame_url"]}, task_id
                    )
                else:
                    frame = await wait_interruptible(asyncio.to_thread(extract_last_frame_tensor, path))
            except Exception:
                frame = await wait_interruptible(asyncio.to_thread(extract_last_frame_tensor, path))
            if frame is None:
                complete = False
                frame = ExecutionBlocker(get_text("err_las_download", task_id=task_id, reason="last frame unavailable"))
        return LASCollection(
            (video, frame, task_id, json.dumps(redact_las_response(task, api_key), ensure_ascii=False)), complete
        )
    except BaseException:
        if path:
            with contextlib.suppress(OSError):
                os.remove(path)
        raise
