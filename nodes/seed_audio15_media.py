import asyncio
import base64
import contextlib
import io
import math
import os
import re
import tempfile
from urllib.parse import urlsplit

import aiohttp
import comfy.model_management
import folder_paths
import torch
from comfy_api.input_impl import VideoFromFile
from comfy_api.latest import Types as comfy_types
from comfy_execution.graph_utils import ExecutionBlocker

from .audio_utils import audio_duration, audio_to_wav_bytes, decode_audio_bytes
from .core_style import PublicOnlyResolver
from .nodes_shared import (
    BytePlusException,
    _tensor2images,
    get_text,
    upload_bytes_to_comfy_storage,
    video_source_size_bytes,
    wait_interruptible,
)
from .seed_audio15_api import MODES, invalid, optional_value
from .utils_download import describe_download_error, download_to_file, public_download_response, validate_public_url

MAX_REFERENCE_AUDIO_BYTES = 15_000_000
MAX_SEPARATION_AUDIO_BYTES = 90_000_000
MAX_IMAGE_BYTES = 10_000_000
MAX_PUBLIC_VIDEO_BYTES = 200_000_000


def url_value(value, video=False):
    if value is None:
        return ""
    if not isinstance(value, str):
        invalid("Media URLs must be text.")
    value = value.strip()
    if not value:
        return ""
    if video and value.startswith("tos://"):
        parsed = urlsplit(value)
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9.-]*[a-z0-9]", parsed.netloc)
            or not parsed.path.lstrip("/")
            or parsed.query
            or parsed.fragment
        ):
            invalid("Use an authorized tos://bucket/object video URI without a query or fragment.")
        return value
    try:
        return validate_public_url(value)
    except (ValueError, aiohttp.InvalidURL):
        invalid("Use a public HTTP(S) media URL without embedded credentials.")


def source(media, url, name, required=False, video=False):
    url = url_value(url, video=video)
    if media is not None and url:
        invalid(f"Connect {name} or set its URL, not both.")
    if required and media is None and not url:
        invalid(f"Connect {name} or set its URL.")
    return url


def audio_data(audio, separation=False):
    if not isinstance(audio, dict):
        invalid("Connect one mono or stereo AUDIO clip.")
    waveform, rate = audio.get("waveform"), audio.get("sample_rate")
    maximum = 360 if separation else 30
    if (
        not isinstance(waveform, torch.Tensor)
        or waveform.ndim != 3
        or waveform.shape[0] != 1
        or waveform.shape[1] not in (1, 2)
        or type(rate) is not int
        or rate <= 0
        or not 2 <= waveform.shape[-1] / rate <= maximum
        or not torch.isfinite(waveform).all()
    ):
        invalid(f"Connect one finite mono or stereo audio clip lasting 2-{maximum} seconds.")
    limit = MAX_SEPARATION_AUDIO_BYTES if separation else MAX_REFERENCE_AUDIO_BYTES
    # Our encoder writes 16-bit PCM WAV. Reject oversized input before copying/encoding it.
    if waveform.numel() * 2 + 44 > limit:
        invalid(f"Encoded audio exceeds {90 if separation else 15} MB. Use a smaller clip.")
    data = audio_to_wav_bytes(audio)
    if len(data) > limit:
        invalid(f"Encoded audio exceeds {90 if separation else 15} MB. Use a smaller clip.")
    return "data:audio/wav;base64," + base64.b64encode(data).decode("ascii")


def image_data(image):
    if not isinstance(image, torch.Tensor) or image.ndim != 4 or image.shape[0] != 1 or image.shape[-1] not in (3, 4):
        invalid("Connect exactly one RGB or RGBA image.")
    _, height, width, _ = image.shape
    if not 300 <= width <= 6000 or not 300 <= height <= 6000 or not 0.4 <= width / height <= 2.5:
        invalid("Image sides must be 300-6000 pixels, with width/height from 0.4 to 2.5.")
    if not torch.isfinite(image).all():
        invalid("Image values must be finite.")
    with io.BytesIO() as buffer:
        _tensor2images(image.detach().cpu().clamp(0, 1))[0].save(buffer, format="PNG")
        data = buffer.getvalue()
    if len(data) >= MAX_IMAGE_BYTES:
        invalid("Encoded image must be smaller than 10 MB.")
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def validate_video(video):
    try:
        duration, fps = float(video.get_duration()), float(video.get_frame_rate())
        width, height = video.get_dimensions()
        size = video_source_size_bytes(video)
    except Exception:
        invalid("Cannot read connected video metadata.")
    if not math.isfinite(duration) or not 4 <= duration <= 360:
        invalid("Video must last 4-360 seconds.")
    if not math.isfinite(fps) or not 12 <= fps <= 60:
        invalid("Video frame rate must be 12-60 FPS.")
    if (
        not 300 <= width <= 6000
        or not 300 <= height <= 6000
        or not 0.4 <= width / height <= 2.5
        or not 407696 <= width * height <= 2086876
    ):
        invalid("Video must have 407696-2086876 pixels, sides of 300-6000 pixels and width/height of 0.4-2.5.")
    if size is not None and size > MAX_PUBLIC_VIDEO_BYTES:
        invalid("Public-URL video input must not exceed 200 MB.")


def prepare_content(options):
    """Validate and encode all local inputs before any upload or paid submission."""
    if not isinstance(options, dict) or options.get("mode") not in MODES:
        invalid("Select a Seed Audio 1.5 mode.")
    mode = options["mode"]
    allowed = {"mode"}
    if mode != "video translation":
        allowed.add("text_prompt")
    if mode in ("audio reference", "video to audio"):
        allowed.update(f"reference_audio_{slot}" for slot in range(1, 7))
        allowed.update(f"ref_audio_{slot}_url" for slot in range(1, 7))
    if mode == "image reference":
        allowed.update(("reference_image", "ref_image_url"))
    if mode in ("video to audio", "video translation"):
        allowed.update(("video", "video_url"))
    if mode == "stem separation":
        allowed.update(("audio", "audio_url"))
    if mode == "video translation":
        allowed.update(
            ("target_language", "source_language", "custom_subtitle_url", "glossaries", "dialogue_enhancement")
        )
    if any(
        key not in allowed and value is not None and not (isinstance(value, str) and not value.strip())
        for key, value in options.items()
    ):
        invalid("Remove inputs that do not belong to the selected mode.")
    content = []
    text = optional_value(options.get("text_prompt"), "")
    if not isinstance(text, str):
        invalid("Prompt must be text.")
    if mode == "video translation":
        if text.strip():
            invalid("Video translation does not accept a text prompt.")
    else:
        if not text.strip():
            invalid("Enter a prompt describing the audio, or the tracks to separate.")
        content.append({"type": "text", "text": text.strip()})
    reference_count = 0
    if mode in ("audio reference", "video to audio"):
        gap = False
        for slot in range(1, 7):
            audio = options.get(f"reference_audio_{slot}")
            url = source(audio, options.get(f"ref_audio_{slot}_url"), f"reference_audio_{slot}")
            if audio is None and not url:
                gap = True
                continue
            if gap:
                invalid("Fill audio reference slots in order, without gaps.")
            if audio is not None:
                url = audio_data(audio)
            reference_count += 1
            content.append({"type": "audio_url", "audio_url": {"url": url}, "role": "reference_audio"})
        if mode == "audio reference" and not reference_count:
            invalid("Connect at least one reference audio clip or set its URL.")
    tags = [int(number) for number in re.findall(r"@audio(\d+)", text, re.IGNORECASE)]
    if tags and (min(tags) < 1 or max(tags) > reference_count):
        invalid("@audioN must refer to a filled reference audio slot (1-6).")
    if mode == "image reference":
        image = options.get("reference_image")
        url = source(image, options.get("ref_image_url"), "reference_image", required=True)
        if image is not None:
            url = image_data(image)
        content.append({"type": "image_url", "image_url": {"url": url}, "role": "reference_image"})
    video = None
    if mode in ("video to audio", "video translation"):
        video = options.get("video")
        url = source(video, options.get("video_url"), "video", required=True, video=True)
        if video is not None:
            validate_video(video)
        # The empty local URL is filled only after all request controls validate.
        content.append(
            {
                "type": "video_url",
                "video_url": {"url": url},
                "role": "dubbing_video" if mode == "video translation" else "reference_video",
            }
        )
    if mode == "stem separation":
        audio = options.get("audio")
        url = source(audio, options.get("audio_url"), "audio", required=True)
        if audio is not None:
            url = audio_data(audio, separation=True)
        content.append({"type": "audio_url", "audio_url": {"url": url}, "role": "separate_audio"})
    return content, video


def video_data(video):
    """Check the exact MP4 bytes to upload, including trims, crops and re-encoding."""
    with io.BytesIO() as buffer:
        video.save_to(buffer, format=comfy_types.VideoContainer.MP4, codec=comfy_types.VideoCodec.H264)
        if buffer.getbuffer().nbytes > MAX_PUBLIC_VIDEO_BYTES:
            invalid("Encoded video exceeds 200 MB. Use a smaller clip or an authorized TOS video URI.")
        return buffer.getvalue()


async def upload_video(node_cls, video):
    try:
        comfy.model_management.throw_exception_if_processing_interrupted()
        data = await wait_interruptible(asyncio.to_thread(video_data, video))
        comfy.model_management.throw_exception_if_processing_interrupted()
        url = await wait_interruptible(
            upload_bytes_to_comfy_storage(
                node_cls,
                data,
                "seed-audio15-reference.mp4",
                "video/mp4",
                cache_kind="seed_audio15_video",
                unavailable_key="err_seed_audio15_upload",
                failed_key="err_seed_audio15_upload",
            )
        )
    except comfy.model_management.InterruptProcessingException:
        raise
    except BytePlusException:
        raise
    except Exception:
        raise BytePlusException(get_text("err_seed_audio15_upload")) from None
    return url_value(url, video=True)


async def download_artifact(url, kind):
    """Public-only, redirect-validated downloads with interruptible waiting."""
    url = url_value(url)
    if not url:
        raise ValueError("Missing artifact URL.")
    directory = os.path.join(folder_paths.get_temp_directory(), "BytePlus", "SeedAudio15")
    os.makedirs(directory, exist_ok=True)
    descriptor, path = tempfile.mkstemp(suffix=".mp4" if kind == "video" else ".bin", dir=directory)
    os.close(descriptor)
    keep = False
    try:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=60, sock_read=120)
        connector = aiohttp.TCPConnector(resolver=PublicOnlyResolver())
        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector, cookie_jar=aiohttp.DummyCookieJar()
        ) as session:
            if kind == "video":
                await download_to_file(session, url, path, timeout=timeout)
            else:
                limit = (8 if kind == "subtitle" else 256) * 1024 * 1024

                async def bounded():
                    count = 0
                    async with public_download_response(session, url, timeout) as response:
                        response.raise_for_status()
                        with open(path, "wb") as handle:
                            async for chunk in response.content.iter_chunked(65536):
                                count += len(chunk)
                                if count > limit:
                                    raise ValueError("Artifact exceeds local download limit.")
                                handle.write(chunk)

                await wait_interruptible(bounded())
        if kind == "video":
            value = VideoFromFile(path)
            await wait_interruptible(asyncio.to_thread(value.get_dimensions))
            keep = True
            return value

        def read_artifact():
            with open(path, "rb") as handle:
                data = handle.read()
            return data.decode("utf-8-sig") if kind == "subtitle" else decode_audio_bytes(data)

        return await wait_interruptible(asyncio.to_thread(read_artifact))
    finally:
        if not keep:
            with contextlib.suppress(OSError):
                os.remove(path)


async def collect_outputs(response, includes):
    content, generation_id = response["content"], response["id"]
    tracks = content["audios"]
    audios, durations, track_types, descriptions, urls = [], [], [], [], []

    async def collect(url, kind):
        try:
            if not isinstance(url, str) or not url.strip():
                raise ValueError("Requested artifact was not returned.")
            return await download_artifact(url, kind)
        except Exception as error:
            return ExecutionBlocker(
                get_text(
                    "err_seed_audio15_download",
                    id=generation_id,
                    reason=describe_download_error(error),
                )
            )

    for track in tracks:
        # A bad track must not discard other paid artifacts or their recovery metadata.
        track = track if isinstance(track, dict) else {}
        url = track.get("url")
        audio = await collect(url, "audio")
        audios.append(audio)
        durations.append(audio_duration(audio) if not isinstance(audio, ExecutionBlocker) else ExecutionBlocker(None))
        track_types.append(str(track.get("type") or ""))
        descriptions.append(str(track.get("description") or ""))
        urls.append(url if isinstance(url, str) else "")
    video = ExecutionBlocker(None)
    if "video_url" in includes:
        video = await collect(content.get("video_url"), "video")
    subtitles = []
    for field in ("subtitles", "subtitles_source"):
        files = content.get(field) or []
        for format_name in ("srt", "jsonl"):
            if f"{field}.{format_name}" not in includes:
                subtitles.append("")
                continue
            matches = (
                [item for item in files if isinstance(item, dict) and item.get("format") == format_name]
                if isinstance(files, list)
                else []
            )
            subtitles.append(await collect(matches[0].get("url") if len(matches) == 1 else None, "subtitle"))
    return (
        audios,
        video,
        *subtitles,
        track_types,
        descriptions,
        durations,
        urls,
    )
