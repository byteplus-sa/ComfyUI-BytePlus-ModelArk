import os
import io
import base64
import asyncio
import aiohttp
import torch
import numpy
import PIL.Image
import folder_paths
import random
import comfy.model_management
from .nodes_shared import log_msg

DEFAULT_DOWNLOAD_TIMEOUT = 60
DEFAULT_DOWNLOAD_RETRIES = 3
# Generated videos can be large: no total time limit, only a stalled connection
# times out (like ComfyUI core's download_url_to_video_output).
VIDEO_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=60, sock_read=120)


def _image_bytes_to_tensor(image_data: bytes) -> torch.Tensor:
    i = PIL.Image.open(io.BytesIO(image_data))
    image = i.convert("RGB")
    image = numpy.array(image).astype(numpy.float32) / 255.0
    return torch.from_numpy(image)[None,]


def _image_bytes_to_rgba_tensor(image_data: bytes) -> torch.Tensor:
    """Decode image bytes to a (1, H, W, 4) RGBA tensor in [0, 1]."""
    image = PIL.Image.open(io.BytesIO(image_data)).convert("RGBA")
    array = numpy.array(image).astype(numpy.float32) / 255.0
    return torch.from_numpy(array)[None,]


async def download_url_to_rgba_tensor_async(
    session: aiohttp.ClientSession, url: str
) -> torch.Tensor | None:
    """
    Download an image and convert it to a (1, H, W, 4) RGBA tensor.
    """
    if not url:
        return None
    try:
        image_data = await _fetch_data_from_url_async(session, url)
        return await asyncio.to_thread(_image_bytes_to_rgba_tensor, image_data)
    except Exception as e:
        log_msg("err_download_url", url=url, e=e)
        return None


async def image_bytes_to_tensor_async(image_data: bytes) -> torch.Tensor | None:
    if not image_data:
        return None
    try:
        return await asyncio.to_thread(_image_bytes_to_tensor, image_data)
    except Exception as e:
        log_msg("err_convert_tensor", e=e)
        return None


async def b64_image_to_tensor_async(b64_data: str) -> torch.Tensor | None:
    if not b64_data:
        return None
    try:
        image_data = base64.b64decode(b64_data)
    except Exception as e:
        log_msg("err_convert_tensor", e=e)
        return None
    return await image_bytes_to_tensor_async(image_data)


async def _fetch_data_from_url_async(
    session: aiohttp.ClientSession,
    url: str,
    timeout: int = DEFAULT_DOWNLOAD_TIMEOUT,
    retries: int = DEFAULT_DOWNLOAD_RETRIES,
) -> bytes:
    """
    Fetch a URL asynchronously, with retries.
    """
    for attempt in range(1, retries + 2):
        try:
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            # t0 = time.time()
            async with session.get(url, timeout=client_timeout) as response:
                response.raise_for_status()
                data = await response.read()
                # t1 = time.time()
                # print(f"[BytePlus Debug] Downloaded {len(data)} bytes from {url} in {t1 - t0:.2f}s")
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt > retries:
                raise e
            retry_delay = 2
            log_msg(
                "download_retry",
                attempt=attempt,
                total=retries + 1,
                delay=retry_delay,
                e=e,
            )
            await asyncio.sleep(retry_delay)
    return b""


async def download_url_to_image_tensor_async(
    session: aiohttp.ClientSession, url: str
) -> torch.Tensor | None:
    """
    Download an image and convert it to a (B, H, W, C) tensor.
    """
    if not url:
        return None
    try:
        image_data = await _fetch_data_from_url_async(session, url)
        return await image_bytes_to_tensor_async(image_data)
    except Exception as e:
        log_msg("err_download_url", url=url, e=e)
        return None


async def _download_to_temp_base(
    session: aiohttp.ClientSession,
    url: str,
    prefix: str,
    seed: int | None,
    save_path_name: str,
    file_ext: str,
) -> tuple[str | None, bytes | None]:
    """
    Download a file into the temp directory.
    """
    if not url:
        return (None, None)

    if seed is not None:
        prefix = f"{prefix}_seed_{seed}"

    output_dir = folder_paths.get_temp_directory()
    if save_path_name:
        output_dir = os.path.join(output_dir, save_path_name)

    (full_output_folder, filename, _, _, _) = folder_paths.get_save_image_path(
        prefix, output_dir
    )
    os.makedirs(full_output_folder, exist_ok=True)

    final_filename = f"{filename}_{random.randint(1, 10000)}.{file_ext}"
    final_path = os.path.join(full_output_folder, final_filename)

    try:
        # t0 = time.time()
        data = await _fetch_data_from_url_async(session, url)
        # t1 = time.time()
        with open(final_path, "wb") as f:
            f.write(data)
        # t2 = time.time()
        # print(f"[BytePlus Debug] Saved to {final_path}. Fetch: {t1 - t0:.2f}s, Write: {t2 - t1:.2f}s")
        return (final_path, data)
    except Exception as e:
        log_msg("err_download_url", url=url, e=e)
        return (None, None)


async def _download_to_file_stream_async(
    session: aiohttp.ClientSession,
    url: str,
    file_path: str,
    timeout: int | aiohttp.ClientTimeout = DEFAULT_DOWNLOAD_TIMEOUT,
    retries: int = DEFAULT_DOWNLOAD_RETRIES,
) -> bool:
    """
    Stream a download to a file. timeout is the limit in seconds for each
    attempt, or an aiohttp.ClientTimeout (e.g. no total limit, only a stall
    limit, for large files).
    """
    for attempt in range(1, retries + 2):
        try:
            if isinstance(timeout, aiohttp.ClientTimeout):
                client_timeout = timeout
            else:
                client_timeout = aiohttp.ClientTimeout(total=timeout)
            # t0 = time.time()
            async with session.get(url, timeout=client_timeout) as response:
                response.raise_for_status()
                with open(file_path, "wb") as f:
                    while True:
                        chunk = await response.content.read(1024 * 1024)  # 1MB chunk
                        if not chunk:
                            break
                        f.write(chunk)
                # t1 = time.time()
                # print(f"[BytePlus Debug] Stream downloaded to {file_path} in {t1 - t0:.2f}s")
                return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt > retries:
                raise e
            retry_delay = 2
            log_msg(
                "download_retry",
                attempt=attempt,
                total=retries + 1,
                delay=retry_delay,
                e=e,
            )
            await asyncio.sleep(retry_delay)
    return False

async def download_video_to_temp(
    session: aiohttp.ClientSession,
    url: str,
    prefix: str,
    seed: int | None,
    save_path_name: str,
) -> str | None:
    """
    Download a video into the temp directory and return its path.
    """
    if not url:
        return None
    file_ext = url.split(".")[-1].split("?")[0] or "mp4"

    if seed is not None:
        prefix = f"{prefix}_seed_{seed}"

    output_dir = folder_paths.get_temp_directory()
    if save_path_name:
        output_dir = os.path.join(output_dir, save_path_name)

    (full_output_folder, filename, _, _, _) = folder_paths.get_save_image_path(
        prefix, output_dir
    )
    os.makedirs(full_output_folder, exist_ok=True)

    final_filename = f"{filename}_{random.randint(1, 10000)}.{file_ext}"
    final_path = os.path.join(full_output_folder, final_filename)

    try:
        success = await _download_to_file_stream_async(session, url, final_path, timeout=VIDEO_DOWNLOAD_TIMEOUT)
        if success:
            return final_path
        return None
    except comfy.model_management.InterruptProcessingException:
        raise
    except Exception as e:
        log_msg("err_download_url", url=url.split("?", 1)[0], e=type(e).__name__)
        return None


async def download_image_to_temp(
    session: aiohttp.ClientSession,
    url: str,
    prefix: str,
    seed: int | None,
    save_path_name: str,
) -> tuple[torch.Tensor | None, str | None]:
    """
    Download an image into the temp directory; return its tensor and path.
    """
    if not url:
        return (None, None)

    file_ext = url.split(".")[-1].split("?")[0] or "jpg"
    (path, data) = await _download_to_temp_base(
        session, url, prefix, seed, save_path_name, file_ext
    )

    tensor = None
    if path and data:
        tensor = await image_bytes_to_tensor_async(data)

    return (tensor, path)


