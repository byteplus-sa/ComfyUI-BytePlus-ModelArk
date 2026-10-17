"""
BytePlus ModelArk 3D generation nodes: Hyper3D Gen2 (text or images to 3D) and
Hitem3D 2.0 (images to 3D).

Both models use the Seedance task endpoint (POST /contents/generations/tasks,
then GET /contents/generations/tasks/{id}). Model options travel as
``--name value`` flags inside the first text item, reference images as
``image_url`` items. A finished task returns ``content.file_url``, a link that
lives 24 hours and points straight to the model file (checked live 2026-10-15: a GLB for
fileformat glb, an OBJ without material file for obj). A zip is also accepted, in case a format ships
with extra files.

The result is extracted to the temp folder and returned as a File3D. ComfyUI's
3D viewer is bound to core's own nodes, so the templates connect it to core's
Preview 3D (Advanced) to view it and Save 3D (Advanced) to download it.
"""

import asyncio
import base64
import io
import json
import os
import re
import shutil
import uuid
import zipfile
from urllib.parse import urlsplit

import aiohttp
import comfy.model_management
import folder_paths

try:
    from comfy_api.latest import Types
except ImportError:
    Types = None
from comfy_api.latest import io as comfy_io
from PIL import Image

from .executor import SEEDANCE_MAX_POLL_ERRORS
from .models_config import (
    HITEM3D_FILE_FORMAT_CODES,
    HITEM3D_MAX_FACE,
    HITEM3D_MAX_IMAGES,
    HITEM3D_MIN_FACE,
    HITEM3D_MODEL,
    HITEM3D_REQUEST_TYPES,
    HITEM3D_RESOLUTIONS,
    HYPER3D_FACE_LIMITS,
    HYPER3D_FILE_FORMATS,
    HYPER3D_MATERIALS,
    HYPER3D_MAX_IMAGES,
    HYPER3D_MAX_PROMPT_CHARS,
    HYPER3D_MAX_SEED,
    HYPER3D_MESH_MODES,
    HYPER3D_MODEL,
    HYPER3D_SUBDIVISION_LEVELS,
)
from .nodes_shared import (
    GLOBAL_CATEGORY,
    LOG_PREFIX,
    BytePlusException,
    billed_ark,
    call_billed,
    format_api_error,
    get_client,
    get_text,
    log_msg,
    plain_text,
    send_node_text,
    sleep_interruptible,
    wait_interruptible,
    with_client,
)
from .utils_download import VIDEO_DOWNLOAD_TIMEOUT, _download_to_file_stream_async

THREE_D_CATEGORY = f"{GLOBAL_CATEGORY}/3D"
THREE_D_POLL_SECONDS = 4
MODEL_EXTENSIONS = ("glb", "gltf", "obj", "fbx", "usdz", "stl")
MAX_EXTRACTED_BYTES = 2 * 1024**3
ZIP_MAGIC = b"PK"
PROMPT_FLAG_PATTERN = re.compile(r"(?:^|\s)--([A-Za-z_]+)")
TERMINAL_FAILURES = ("failed", "cancelled", "expired")


def render_command_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(str(item) for item in value) + "]"
    return str(value)


def build_text_command(params):
    """``--name value`` flags in the given order; None values are skipped (the API default applies)."""
    return " ".join(f"--{name} {render_command_value(value)}" for name, value in params.items() if value is not None)


def build_text_item(prompt, params):
    text = " ".join(part for part in (str(prompt or "").strip(), build_text_command(params)) if part)
    return {"type": "text", "text": text}


def raise_if_prompt_has_flags(prompt):
    match = PROMPT_FLAG_PATTERN.search(str(prompt or ""))
    if match:
        raise BytePlusException(get_text("err3d_prompt_flags", flag=match.group(1)))


def image_batch_to_items(images):
    """Every image of an IMAGE batch as an image_url data URI (PNG when it has an alpha channel, else JPEG)."""
    if images is None:
        return []
    items = []
    for frame in images:
        array = (frame.clamp(0, 1).cpu().numpy() * 255).astype("uint8")
        has_alpha = array.shape[-1] == 4
        picture = Image.fromarray(array, "RGBA" if has_alpha else "RGB")
        buffer = io.BytesIO()
        picture.save(buffer, format="PNG" if has_alpha else "JPEG")
        mime = "image/png" if has_alpha else "image/jpeg"
        encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
        items.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
    return items


def image_count(images):
    return 0 if images is None else int(images.shape[0])


def hyper3d_face_count(mesh_mode, quality_override):
    """None when unset (0); otherwise the count, checked against the mesh mode's range."""
    if not quality_override:
        return None
    low, high = HYPER3D_FACE_LIMITS[mesh_mode]
    if not low <= quality_override <= high:
        raise BytePlusException(get_text("err3d_face_range", mode=mesh_mode, min=low, max=high, count=quality_override))
    return int(quality_override)


def build_hyper3d_request(
    prompt,
    images,
    mesh_mode,
    material,
    file_format,
    subdivision_level,
    quality_override,
    hd_texture,
    addons,
    ta_pose,
    use_original_alpha,
    bbox,
    seed,
):
    prompt = str(prompt or "").strip()
    count = image_count(images)
    if not prompt and not count:
        raise BytePlusException(get_text("err3d_no_input", node="BytePlus Hyper3D"))
    if len(prompt) > HYPER3D_MAX_PROMPT_CHARS:
        raise BytePlusException(get_text("err3d_prompt_too_long", count=len(prompt), max=HYPER3D_MAX_PROMPT_CHARS))
    if count > HYPER3D_MAX_IMAGES:
        raise BytePlusException(
            get_text("err3d_too_many_images", node="BytePlus Hyper3D", max=HYPER3D_MAX_IMAGES, count=count)
        )
    raise_if_prompt_has_flags(prompt)
    face_count = hyper3d_face_count(mesh_mode, quality_override)
    params = {
        "mesh_mode": mesh_mode,
        "material": material,
        "fileformat": file_format,
        "subdivisionlevel": None if face_count else subdivision_level,
        "quality_override": face_count,
        "hd_texture": hd_texture,
        "addons": "HighPack" if addons else None,
        "TAPose": ta_pose,
        "use_original_alpha": use_original_alpha if count else None,
        "bbox_condition": list(bbox) if bbox and all(bbox) else None,
    }
    content = []
    text_item = build_text_item(prompt, params)
    content.append(text_item)
    content.extend(image_batch_to_items(images))
    return {"model": HYPER3D_MODEL, "content": content, "seed": seed}


def derive_views(count):
    return ("1" * count).ljust(HITEM3D_MAX_IMAGES, "0")


def hitem3d_views(views, count):
    """Views bitmap (front, back, left, right): given and checked, or the first ``count`` views."""
    views = str(views or "").strip()
    if not views:
        return derive_views(count)
    if len(views) != HITEM3D_MAX_IMAGES or set(views) - {"0", "1"} or views.count("1") != count:
        raise BytePlusException(get_text("err3d_views_invalid", views=views, count=count))
    return views


def build_hitem3d_request(images, resolution, request_type, file_format, face, views):
    count = image_count(images)
    if not count:
        raise BytePlusException(get_text("err3d_no_images", node="BytePlus Hitem3D"))
    if count > HITEM3D_MAX_IMAGES:
        raise BytePlusException(
            get_text("err3d_too_many_images", node="BytePlus Hitem3D", max=HITEM3D_MAX_IMAGES, count=count)
        )
    params = {
        "resolution": resolution,
        "request_type": HITEM3D_REQUEST_TYPES[request_type],
        "fileformat": HITEM3D_FILE_FORMAT_CODES[file_format],
        "face": int(face) if face else None,
        "multi_images_bit": hitem3d_views(views, count) if count > 1 or str(views or "").strip() else None,
    }
    content = [build_text_item("", params), *image_batch_to_items(images)]
    return {"model": HITEM3D_MODEL, "content": content}


def task_field(source, name):
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def task_file_url(task):
    """content.file_url, which the API may give as a string or as {"url": ...}."""
    value = task_field(task_field(task, "content"), "file_url")
    if isinstance(value, dict):
        value = value.get("url")
    return value if isinstance(value, str) and value else None


def task_error_reason(task):
    error = task_field(task, "error")
    message = task_field(error, "message") if error is not None else None
    code = task_field(error, "code") if error is not None else None
    return " ".join(str(part) for part in (code, message) if part) or "no reason given"


def poll_error_is_permanent(error):
    status = getattr(error, "status_code", None)
    return isinstance(status, int) and 400 <= status < 500 and status not in (408, 409, 429)


async def cancel_task(client, task_id):
    try:
        await asyncio.to_thread(client.ark.content_generation.tasks.delete, task_id=task_id)
        log_msg("msg3d_cancelled", task_id=task_id)
    except Exception as error:
        log_msg("msg3d_cancel_failed", task_id=task_id, reason=format_api_error(error).removeprefix(LOG_PREFIX))


async def cancel_created_task(client, creation):
    """After an interrupt the worker thread still creates (and bills) the task: wait for it, then cancel it."""
    try:
        task = await creation
    except (Exception, comfy.model_management.InterruptProcessingException):
        return
    await cancel_task(client, task.id)


async def submit_task(client, request):
    extra = {key: value for key, value in request.items() if key not in ("model", "content") and value is not None}
    creation = asyncio.ensure_future(
        asyncio.to_thread(
            call_billed,
            billed_ark(client).content_generation.tasks.create,
            model=request["model"],
            content=request["content"],
            **extra,
        )
    )
    try:
        task = await wait_interruptible(asyncio.shield(creation))
    except comfy.model_management.InterruptProcessingException:
        await cancel_created_task(client, creation)
        raise
    except Exception as error:
        raise BytePlusException(format_api_error(error)) from error
    return task.id


async def wait_for_task(client, task_id, node_id=None):
    """Poll until the task succeeds and return its file URL; cancels the task on interrupt."""
    failed_polls = 0
    last_status = None
    try:
        while True:
            comfy.model_management.throw_exception_if_processing_interrupted()
            try:
                task = await asyncio.to_thread(client.ark.content_generation.tasks.get, task_id=task_id)
                failed_polls = 0
            except comfy.model_management.InterruptProcessingException:
                raise
            except Exception as error:
                failed_polls += 1
                if poll_error_is_permanent(error) or failed_polls >= SEEDANCE_MAX_POLL_ERRORS:
                    raise BytePlusException(
                        get_text("err3d_poll_failed", task_id=task_id, reason=format_api_error(error))
                    ) from error
                await sleep_interruptible(THREE_D_POLL_SECONDS)
                continue
            status = task_field(task, "status")
            if status != last_status:
                last_status = status
                send_node_text(node_id, plain_text("msg3d_status", task_id=task_id, status=status))
            if status == "succeeded":
                file_url = task_file_url(task)
                if not file_url:
                    raise BytePlusException(get_text("err3d_no_file_url", task_id=task_id))
                return file_url
            if status in TERMINAL_FAILURES:
                if status == "failed":
                    raise BytePlusException(
                        get_text("err3d_task_failed", task_id=task_id, reason=task_error_reason(task))
                    )
                raise BytePlusException(get_text("err3d_task_ended", task_id=task_id, status=status))
            await sleep_interruptible(THREE_D_POLL_SECONDS)
    except comfy.model_management.InterruptProcessingException:
        await cancel_task(client, task_id)
        raise


def new_result_dir(task_id):
    folder = os.path.join(folder_paths.get_temp_directory(), "BytePlus", "3d", f"{task_id}_{uuid.uuid4().hex[:8]}")
    os.makedirs(folder, exist_ok=True)
    return folder


async def download_result(url, destination, task_id):
    async with aiohttp.ClientSession() as session:
        try:
            await wait_interruptible(
                _download_to_file_stream_async(session, url, destination, timeout=VIDEO_DOWNLOAD_TIMEOUT)
            )
        except comfy.model_management.InterruptProcessingException:
            raise
        except Exception as error:
            raise BytePlusException(
                get_text("err3d_download_failed", task_id=task_id, reason=type(error).__name__)
            ) from error


def is_zip_file(path):
    with open(path, "rb") as handle:
        return handle.read(2) == ZIP_MAGIC


def extract_archive(archive_path, folder):
    """Extract a result zip into folder, refusing paths outside it and oversized archives."""
    root = os.path.realpath(folder)
    with zipfile.ZipFile(archive_path) as archive:
        members = [member for member in archive.infolist() if not member.is_dir()]
        if sum(member.file_size for member in members) > MAX_EXTRACTED_BYTES:
            raise BytePlusException(get_text("err3d_archive_too_large", max_mb=MAX_EXTRACTED_BYTES // 1024**2))
        for member in members:
            target = os.path.realpath(os.path.join(root, member.filename))
            if os.path.commonpath([root, target]) != root:
                raise BytePlusException(get_text("err3d_archive_unsafe", name=member.filename))
        archive.extractall(root, members)
    return [os.path.join(dirpath, name) for dirpath, _, names in os.walk(folder) for name in names]


def pick_model_file(paths, file_format):
    """The model file to return: the largest file of the requested format, else of any 3D format."""

    def largest(extensions):
        matches = [path for path in paths if os.path.splitext(path)[1].lstrip(".").lower() in extensions]
        return max(matches, key=os.path.getsize) if matches else None

    return largest((file_format,)) or largest(MODEL_EXTENSIONS)


def result_file_name(url, file_format):
    """result.<ext>: the extension of the result link when it names a 3D format, else the requested format."""
    extension = os.path.splitext(urlsplit(url).path)[1].lstrip(".").lower()
    return f"result.{extension if extension in MODEL_EXTENSIONS else file_format}"


def load_result(download_path, folder, task_id, file_format, plain_name=None):
    """The result as a File3D plus the path of its model file.

    A zip is extracted into folder (a USDZ file is a zip but is the model itself). Any other file is the model itself and is
    renamed to plain_name (default result.<file_format>), so it has an extension.
    """
    plain_name = plain_name or f"result.{file_format}"
    is_usdz = plain_name.lower().endswith(".usdz")
    if is_zip_file(download_path) and not is_usdz:
        paths = extract_archive(download_path, folder)
    else:
        model_file = os.path.join(folder, plain_name)
        os.replace(download_path, model_file)
        paths = [model_file]
    model_path = pick_model_file(paths, file_format)
    if model_path is None:
        names = ", ".join(sorted(os.path.basename(path) for path in paths)) or "nothing"
        raise BytePlusException(get_text("err3d_no_model_in_archive", task_id=task_id, names=names))
    model_format = os.path.splitext(model_path)[1].lstrip(".").lower()
    return Types.File3D(model_path, model_format), model_path


async def run_3d_task(client, request, file_format, node_id):
    """Submit, wait, download and unpack one task: (File3D, task ID)."""
    comfy.model_management.throw_exception_if_processing_interrupted()
    task_id = await submit_task(client, request)
    send_node_text(node_id, plain_text("msg3d_status", task_id=task_id, status="submitted"))
    file_url = await wait_for_task(client, task_id, node_id)
    folder = new_result_dir(task_id)
    download_path = os.path.join(folder, "result.download")
    try:
        await download_result(file_url, download_path, task_id)
        model, model_path = load_result(
            download_path, folder, task_id, file_format, result_file_name(file_url, file_format)
        )
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return model, task_id


def result_json(task_id, model, file_format):
    return json.dumps({"task_id": task_id, "file_format": model.format or file_format})


def model_output():
    return comfy_io.File3DAny.Output(
        "model",
        display_name="model",
        tooltip="The generated 3D model. Connect it to Save 3D Model to keep it, or to any 3D node.",
    )


def response_output():
    return comfy_io.String.Output("response", tooltip="Task ID and format of the result, as JSON.")


class BytePlusHyper3D(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusHyper3D",
            display_name="BytePlus Hyper3D Gen2",
            search_aliases=["Hyper3D", "Rodin", "text to 3D", "image to 3D"],
            category=THREE_D_CATEGORY,
            description="Generate a textured 3D model with Hyper3D Gen2 from a text prompt, from up to "
            f"{HYPER3D_MAX_IMAGES} reference images, or from both.",
            inputs=[
                comfy_io.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    optional=True,
                    tooltip=f"Describe the model in English (up to {HYPER3D_MAX_PROMPT_CHARS} characters). "
                    "Required when no image is connected.",
                ),
                comfy_io.Image.Input(
                    "images",
                    optional=True,
                    tooltip=f"Up to {HYPER3D_MAX_IMAGES} reference images of one object (a batch). "
                    "A clean, single-object picture on a plain background works best.",
                ),
                comfy_io.Combo.Input(
                    "mesh_mode",
                    options=HYPER3D_MESH_MODES,
                    default="Quad",
                    tooltip="Quad: clean quad topology for editing. Raw: triangles.",
                ),
                comfy_io.Combo.Input(
                    "material",
                    options=HYPER3D_MATERIALS,
                    default="PBR",
                    tooltip="PBR: physically based textures. Shaded: baked lighting. All: both. None: white model.",
                ),
                comfy_io.Combo.Input(
                    "file_format",
                    options=HYPER3D_FILE_FORMATS,
                    default="glb",
                    tooltip="Format of the 3D file. GLB is the default.",
                ),
                comfy_io.Int.Input(
                    "seed",
                    default=0,
                    min=0,
                    max=HYPER3D_MAX_SEED,
                    control_after_generate=True,
                    optional=True,
                    tooltip="Seed for repeatable results (0-65535).",
                ),
                comfy_io.Combo.Input(
                    "subdivision_level",
                    options=HYPER3D_SUBDIVISION_LEVELS,
                    default="medium",
                    optional=True,
                    advanced=True,
                    tooltip="Polygon count level. Ignored when quality_override is set.",
                ),
                comfy_io.Int.Input(
                    "quality_override",
                    default=0,
                    min=0,
                    max=1000000,
                    optional=True,
                    advanced=True,
                    tooltip="Exact face count. 0 = use subdivision_level. Raw: 500-1,000,000. Quad: 1,000-200,000.",
                ),
                comfy_io.Boolean.Input(
                    "hd_texture",
                    default=False,
                    optional=True,
                    advanced=True,
                    tooltip="Generate higher resolution textures.",
                ),
                comfy_io.Boolean.Input(
                    "high_pack",
                    default=False,
                    optional=True,
                    advanced=True,
                    tooltip="HighPack add-on: 4K textures.",
                ),
                comfy_io.Boolean.Input(
                    "ta_pose",
                    default=False,
                    optional=True,
                    advanced=True,
                    tooltip="Force a T-pose or A-pose for humanoid characters.",
                ),
                comfy_io.Boolean.Input(
                    "use_original_alpha",
                    default=False,
                    optional=True,
                    advanced=True,
                    tooltip="Keep the transparent areas of the input images (only used with images).",
                ),
                comfy_io.Int.Input(
                    "bbox_width",
                    default=0,
                    min=0,
                    max=10000,
                    optional=True,
                    advanced=True,
                    tooltip="Bounding box width. Set width, height and length together; 0 = off.",
                ),
                comfy_io.Int.Input(
                    "bbox_height",
                    default=0,
                    min=0,
                    max=10000,
                    optional=True,
                    advanced=True,
                    tooltip="Bounding box height. Set width, height and length together; 0 = off.",
                ),
                comfy_io.Int.Input(
                    "bbox_length",
                    default=0,
                    min=0,
                    max=10000,
                    optional=True,
                    advanced=True,
                    tooltip="Bounding box length. Set width, height and length together; 0 = off.",
                ),
            ],
            outputs=[model_output(), response_output()],
            hidden=[comfy_io.Hidden.unique_id],
        )

    @classmethod
    @with_client("client", get_client)
    async def execute(
        cls,
        client,
        prompt="",
        images=None,
        mesh_mode="Quad",
        material="PBR",
        file_format="glb",
        seed=0,
        subdivision_level="medium",
        quality_override=0,
        hd_texture=False,
        high_pack=False,
        ta_pose=False,
        use_original_alpha=False,
        bbox_width=0,
        bbox_height=0,
        bbox_length=0,
    ) -> comfy_io.NodeOutput:
        request = build_hyper3d_request(
            prompt,
            images,
            mesh_mode,
            material,
            file_format,
            subdivision_level,
            quality_override,
            hd_texture or None,
            high_pack,
            ta_pose or None,
            use_original_alpha,
            (bbox_width, bbox_height, bbox_length),
            seed,
        )
        model, task_id = await run_3d_task(client, request, file_format, cls.hidden.unique_id)
        return comfy_io.NodeOutput(model, result_json(task_id, model, file_format))


class BytePlusHitem3D(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusHitem3D",
            display_name="BytePlus Hitem3D 2.0",
            search_aliases=["Hitem3D", "image to 3D", "multi view to 3D"],
            category=THREE_D_CATEGORY,
            description=f"Generate a 3D model with Hitem3D 2.0 from 1 to {HITEM3D_MAX_IMAGES} images of one object "
            "(front, back, left, right).",
            inputs=[
                comfy_io.Image.Input(
                    "images",
                    tooltip=f"1 to {HITEM3D_MAX_IMAGES} images of one object (a batch), in the order front, back, "
                    "left, right. With several images, set views if they are not the first views.",
                ),
                comfy_io.Combo.Input(
                    "resolution",
                    options=HITEM3D_RESOLUTIONS,
                    default="1536",
                    tooltip="Model resolution: 1536 (default) or 1536pro.",
                ),
                comfy_io.Combo.Input(
                    "request_type",
                    options=list(HITEM3D_REQUEST_TYPES),
                    default="Geometry and texture",
                    tooltip="Geometry only returns an untextured model.",
                ),
                comfy_io.Combo.Input(
                    "file_format",
                    options=list(HITEM3D_FILE_FORMAT_CODES),
                    default="glb",
                    tooltip="Format of the 3D file. GLB is the default.",
                ),
                comfy_io.Int.Input(
                    "face",
                    default=0,
                    min=0,
                    max=HITEM3D_MAX_FACE,
                    optional=True,
                    advanced=True,
                    tooltip=f"Face count ({HITEM3D_MIN_FACE:,}-{HITEM3D_MAX_FACE:,}). 0 = the model's default.",
                ),
                comfy_io.String.Input(
                    "views",
                    default="",
                    optional=True,
                    advanced=True,
                    tooltip="Which views the images are, as 4 digits for front, back, left, right: 1010 = front "
                    "and left. One 1 per image. Empty = the first views in order.",
                ),
            ],
            outputs=[model_output(), response_output()],
            hidden=[comfy_io.Hidden.unique_id],
        )

    @classmethod
    @with_client("client", get_client)
    async def execute(
        cls,
        client,
        images,
        resolution="1536",
        request_type="Geometry and texture",
        file_format="glb",
        face=0,
        views="",
    ) -> comfy_io.NodeOutput:
        if face and not HITEM3D_MIN_FACE <= face <= HITEM3D_MAX_FACE:
            raise BytePlusException(
                get_text("err3d_face_range", mode="Hitem3D", min=HITEM3D_MIN_FACE, max=HITEM3D_MAX_FACE, count=face)
            )
        request = build_hitem3d_request(images, resolution, request_type, file_format, face, views)
        model, task_id = await run_3d_task(client, request, file_format, cls.hidden.unique_id)
        return comfy_io.NodeOutput(model, result_json(task_id, model, file_format))


NODES = (
    [BytePlusHyper3D, BytePlusHitem3D]
    if getattr(Types, "File3D", None) is not None and hasattr(comfy_io, "File3DAny")
    else []
)
