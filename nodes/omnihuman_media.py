import asyncio
import io
import os
import tempfile
from dataclasses import dataclass

import aiohttp
import comfy.model_management
import folder_paths
import numpy
import PIL.Image
import torch
from comfy_api.input_impl import VideoFromFile
from comfy_execution.graph_utils import ExecutionBlocker

from .audio_utils import audio_to_wav_bytes
from .core_style import PublicOnlyResolver
from .nodes_shared import BytePlusException, get_text, upload_bytes_to_comfy_storage, wait_interruptible
from .omnihuman_api import invalid, validate_url
from .utils_download import describe_download_error, download_to_file

IMAGE_MAX_BYTES = 30_000_000
AUDIO_MAX_BYTES = 15_000_000


@dataclass(frozen=True)
class PreparedMedia:
    image_url: str
    audio_url: str
    image_bytes: bytes | None = None
    audio_bytes: bytes | None = None


def _source(media, url, name):
    if not isinstance(url, str):
        invalid(f"{name}_url must be text.")
    url = url.strip()
    if media is not None and url:
        invalid(f"Connect {name} or set {name}_url, not both.")
    if media is None and not url:
        invalid(f"Connect {name} or set {name}_url.")
    return validate_url(url) if url else ""


def encode_image(image):
    if not isinstance(image, torch.Tensor) or image.ndim != 4 or image.shape[0] != 1 or image.shape[-1] not in (3, 4):
        invalid("Connect exactly one RGB or RGBA image.")
    _, height, width, _ = image.shape
    if not 300 <= width <= 6000 or not 300 <= height <= 6000 or not 0.4 <= width / height <= 2.5:
        invalid("Image dimensions must be 300-6000 pixels and width/height must be 0.4-2.5.")
    if not torch.isfinite(image).all():
        invalid("Image values must be finite.")
    array = (image[0].detach().cpu().clamp(0, 1).numpy() * 255).round().astype(numpy.uint8)
    with io.BytesIO() as buffer:
        PIL.Image.fromarray(array).save(buffer, format="PNG")
        data = buffer.getvalue()
    if len(data) >= IMAGE_MAX_BYTES:
        invalid("Encoded image must be smaller than 30 MB.")
    return data


def encode_audio(audio):
    if not isinstance(audio, dict):
        invalid("Audio must contain waveform and sample_rate.")
    waveform, sample_rate = audio.get("waveform"), audio.get("sample_rate")
    if (
        not isinstance(waveform, torch.Tensor)
        or waveform.ndim != 3
        or waveform.shape[0] != 1
        or waveform.shape[1] not in (1, 2)
        or type(sample_rate) is not int
        or sample_rate <= 0
        or not 4 <= waveform.shape[-1] / sample_rate <= 300
    ):
        invalid("Connect one mono or stereo audio clip lasting 4-300 seconds, with a positive sample rate.")
    if not torch.isfinite(waveform).all():
        invalid("Audio values must be finite.")
    encoded_size = 44 + waveform.shape[1] * waveform.shape[-1] * 2
    oversized = encoded_size >= AUDIO_MAX_BYTES
    data = audio_to_wav_bytes(audio, sample_rate=min(sample_rate, 24000) if oversized else None, mono=oversized)
    if len(data) >= AUDIO_MAX_BYTES:
        invalid("Encoded audio must be smaller than 15 MB.")
    return data


async def prepare_media(image, audio, image_url, audio_url):
    image_url = _source(image, image_url, "image")
    audio_url = _source(audio, audio_url, "audio")
    image_bytes = await wait_interruptible(asyncio.to_thread(encode_image, image)) if image is not None else None
    audio_bytes = await wait_interruptible(asyncio.to_thread(encode_audio, audio)) if audio is not None else None
    return PreparedMedia(image_url, audio_url, image_bytes, audio_bytes)


async def upload_media(node_cls, prepared):
    urls = []
    for kind, data, reference, extension, mime in (
        ("image", prepared.image_bytes, prepared.image_url, "png", "image/png"),
        ("audio", prepared.audio_bytes, prepared.audio_url, "wav", "audio/wav"),
    ):
        if data is None:
            urls.append(reference)
            continue
        try:
            reference = await wait_interruptible(
                upload_bytes_to_comfy_storage(
                    node_cls,
                    data,
                    f"omnihuman.{extension}",
                    mime,
                    cache_kind=f"omnihuman_{kind}",
                    unavailable_key="err_omnihuman_upload",
                    failed_key="err_omnihuman_upload",
                    kind=kind,
                )
            )
        except comfy.model_management.InterruptProcessingException:
            raise
        except Exception:
            raise BytePlusException(get_text("err_omnihuman_upload", kind=kind)) from None
        urls.append(validate_url(reference))
    return tuple(urls)


async def collect_video(task, task_id):
    data = task["data"]
    status = data["status"]
    if status in ("in_queue", "generating"):
        return ExecutionBlocker(get_text("omnihuman_waiting", task_id=task_id, status=status)), False
    if status != "done":
        raise BytePlusException(get_text("err_omnihuman_task", task_id=task_id, detail=f"Task is {status}."))
    url = validate_url(data.get("video_url"))
    directory = os.path.join(folder_paths.get_temp_directory(), "BytePlus", "OmniHuman")
    os.makedirs(directory, exist_ok=True)
    descriptor, path = tempfile.mkstemp(suffix=".mp4", dir=directory)
    os.close(descriptor)
    try:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=60, sock_read=120)
        connector = aiohttp.TCPConnector(resolver=PublicOnlyResolver())
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            await download_to_file(session, url, path, timeout=timeout)
        video = VideoFromFile(path)
        await wait_interruptible(asyncio.to_thread(video.get_dimensions))
        return video, True
    except comfy.model_management.InterruptProcessingException:
        if os.path.exists(path):
            os.unlink(path)
        raise
    except Exception as error:
        if os.path.exists(path):
            os.unlink(path)
        return ExecutionBlocker(
            get_text("err_omnihuman_download", task_id=task_id, reason=describe_download_error(error))
        ), False
