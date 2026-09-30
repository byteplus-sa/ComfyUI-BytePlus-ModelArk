"""
Seed LLM node shaped like ComfyUI core's ByteDanceSeedNode (nodes_bytedance_llm.py).

Same inputs as core (prompt, model with images / videos / temperature, seed,
system_prompt), with the API Client socket first and this pack's extras after
core's inputs as advanced widgets. Calls the ModelArk Responses API directly
with the user's key; images and videos go through the Ark Files API.
"""
import asyncio
import hashlib
import io
import json
import os
import uuid

from comfy_api.latest import io as comfy_io

from .core_style import seed_input
from .executor import BytePlusVisualExecutor
from .models_config import (
    SEED_LLM_MAX_IMAGES,
    SEED_LLM_MAX_VIDEOS,
    SEED_LLM_MODEL_MAP,
    SEED_LLM_NO_REASONING_EFFORT,
    SEED_LLM_UI_OPTIONS,
)
from .nodes_shared import (
    GLOBAL_CATEGORY,
    BytePlusClientType,
    BytePlusException,
    _tensor2images,
    get_node_count_in_workflow,
    get_text,
    log_msg,
    upload_file_to_ark,
)
from .nodes_visual import _conversation_owner

FILE_EXPIRE_MIN_SECONDS = 86400
FILE_EXPIRE_MAX_SECONDS = 2592000
FILE_EXPIRE_DEFAULT_SECONDS = 604800

# Last stored response per node, for multi-turn (previous_response_id). Keyed by
# node id; the owner (key fingerprint, region) must match to continue.
SEED_LAST_RESPONSES = {}
SEED_LAST_RESPONSES_MAX = 256


def _seed_model_inputs(max_images=SEED_LLM_MAX_IMAGES, max_videos=SEED_LLM_MAX_VIDEOS):
    """Core's per-model inputs: Autogrow images and videos, then temperature."""
    return [
        comfy_io.Autogrow.Input(
            "images",
            template=comfy_io.Autogrow.TemplateNames(
                comfy_io.Image.Input("image"),
                names=[f"image_{i}" for i in range(1, max_images + 1)],
                min=0,
            ),
            tooltip=f"Optional image(s) to use as context for the model. Up to {max_images} images.",
        ),
        comfy_io.Autogrow.Input(
            "videos",
            template=comfy_io.Autogrow.TemplateNames(
                comfy_io.Video.Input("video"),
                names=[f"video_{i}" for i in range(1, max_videos + 1)],
                min=0,
            ),
            tooltip=f"Optional video(s) to use as context for the model. Up to {max_videos} videos.",
        ),
        comfy_io.Float.Input(
            "temperature",
            default=1.0,
            min=0.0,
            max=2.0,
            step=0.01,
            tooltip="Controls randomness. 0.0 is deterministic, higher values are more random.",
            advanced=True,
        ),
    ]


def _extra_inputs():
    """
    This pack's extras, after core's inputs. Optional (widgets still always send a
    value) so the frontend lists them after core's optional system_prompt.
    """
    return [
        comfy_io.Combo.Input(
            "detail",
            options=["low", "high"],
            default="high",
            tooltip="Image detail level sent with each image.",
            optional=True,
            advanced=True,
        ),
        comfy_io.Float.Input(
            "fps",
            default=1.0,
            min=0.2,
            max=5.0,
            step=0.1,
            tooltip="Frames per second sampled from each video.",
            optional=True,
            advanced=True,
        ),
        comfy_io.Combo.Input(
            "reasoning_mode",
            options=["auto", "enabled", "disabled"],
            default="auto",
            tooltip="Deep thinking (thinking.type). auto sends nothing: the model default (enabled) applies.",
            optional=True,
            advanced=True,
        ),
        comfy_io.Combo.Input(
            "reasoning_effort",
            options=["minimal", "low", "medium", "high"],
            default="medium",
            tooltip=(
                "Chain-of-thought length (reasoning.effort). Not sent when reasoning_mode is "
                "disabled, or for Seed 1.6 Flash, which does not support it."
            ),
            optional=True,
            advanced=True,
        ),
        comfy_io.Int.Input(
            "turns",
            default=1,
            min=1,
            max=10,
            tooltip=(
                "1: every run starts a new conversation (the response is not stored). "
                "2 or more: each run continues this node's last conversation with the new "
                "prompt; images and videos from the first turn stay in the conversation."
            ),
            optional=True,
            advanced=True,
        ),
        comfy_io.Boolean.Input(
            "stream",
            default=False,
            tooltip="Stream the answer to the console while it is generated.",
            optional=True,
            advanced=True,
        ),
        comfy_io.Int.Input(
            "file_expire_seconds",
            default=FILE_EXPIRE_DEFAULT_SECONDS,
            min=FILE_EXPIRE_MIN_SECONDS,
            max=FILE_EXPIRE_MAX_SECONDS,
            step=1,
            tooltip="How long uploaded images and videos are kept in Ark Files (1 to 30 days).",
            optional=True,
            advanced=True,
        ),
    ]


def _media_cache_dir():
    import folder_paths

    path = os.path.join(folder_paths.get_temp_directory(), "BytePlusSeed")
    os.makedirs(path, exist_ok=True)
    return path


def _content_path(digest, extension):
    return os.path.join(_media_cache_dir(), f"byteplus_seed_{digest}{extension}")


def image_files(image_tensors):
    """Every image of every connected batch -> a JPEG file named by its content hash."""
    paths = []
    for tensor in image_tensors:
        for picture in _tensor2images(tensor):
            with io.BytesIO() as buffer:
                picture.convert("RGB").save(buffer, format="JPEG", quality=95)
                data = buffer.getvalue()
            path = _content_path(hashlib.sha256(data).hexdigest(), ".jpg")
            if not os.path.exists(path):
                with open(path, "wb") as f:
                    f.write(data)
            paths.append(path)
    return paths


def video_file(video):
    """
    A VIDEO input -> an MP4 file named by its content hash. MP4 sources are
    stream-copied (no re-encode); trims, crops and other containers are encoded.
    """
    from comfy_api.latest import Types

    temp_path = os.path.join(_media_cache_dir(), f"byteplus_seed_{uuid.uuid4().hex}.part.mp4")
    try:
        video.save_to(temp_path, format=Types.VideoContainer.MP4)
        hasher = hashlib.sha256()
        with open(temp_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                hasher.update(chunk)
        path = _content_path(hasher.hexdigest(), ".mp4")
        os.replace(temp_path, path)
        return path
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def image_count(image_tensors):
    return sum(int(t.shape[0]) if getattr(t, "ndim", 0) == 4 else 1 for t in image_tensors)


def build_seed_payload(model_id, content, temperature, system_prompt="", reasoning_mode="auto",
                       reasoning_effort="medium", supports_effort=True, previous_response_id=None,
                       store=None):
    """Responses API request for the Seed node (SDK responses.create kwargs)."""
    payload = {
        "model": model_id,
        "input": [{"role": "user", "content": content}],
        "temperature": float(temperature),
    }
    if (system_prompt or "").strip():
        # Not carried over by previous_response_id, so it is sent every turn.
        payload["instructions"] = system_prompt
    if previous_response_id:
        payload["previous_response_id"] = previous_response_id
    if store is not None:
        payload["store"] = store
    if reasoning_mode != "auto":
        payload["thinking"] = {"type": reasoning_mode}
    # With thinking disabled the API only accepts effort "minimal", which is the same thing.
    if supports_effort and reasoning_mode != "disabled":
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload


def response_text(response):
    """Text of all assistant output_text blocks; API errors and refusals raise."""
    if not isinstance(response, dict):
        return ""
    error = response.get("error")
    if error:
        code = error.get("code") if isinstance(error, dict) else ""
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise BytePlusException(get_text("seed_llm_api_error", code=code or "-", message=message or ""))
    output = response.get("output")
    if not isinstance(output, list):
        return ""
    chunks = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message" and item.get("role") != "assistant":
            continue
        for block in item.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "output_text" and block.get("text"):
                chunks.append(block["text"])
            elif block.get("type") == "refusal" and block.get("refusal"):
                raise BytePlusException(get_text("seed_llm_refusal", refusal=block["refusal"]))
    return "\n".join(chunks)


class BytePlusSeed(comfy_io.ComfyNode):
    """Text responses from BytePlus Seed models (Responses API)."""

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusSeed",
            display_name="BytePlus Seed",
            category=GLOBAL_CATEGORY,
            description=(
                "Generate text responses with BytePlus Seed models (Seed 2.0 Pro, Lite and Mini, "
                "Seed 2.1 Turbo, Seed 1.8, Seed 1.6). Provide a text prompt and optionally one or "
                "more images or videos for multimodal context."
            ),
            inputs=[
                BytePlusClientType.Input("client"),
                comfy_io.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip="Text input to the model.",
                ),
                comfy_io.DynamicCombo.Input(
                    "model",
                    options=[
                        comfy_io.DynamicCombo.Option(label, _seed_model_inputs())
                        for label in SEED_LLM_UI_OPTIONS
                    ],
                    tooltip="The Seed model used to generate the response.",
                ),
                seed_input(),
                comfy_io.String.Input(
                    "system_prompt",
                    multiline=True,
                    default="",
                    optional=True,
                    advanced=True,
                    tooltip="Foundational instructions that dictate the model's behavior.",
                ),
                *_extra_inputs(),
            ],
            outputs=[
                comfy_io.String.Output(),
                comfy_io.String.Output(display_name="raw_json"),
            ],
            hidden=[comfy_io.Hidden.unique_id, comfy_io.Hidden.prompt],
        )

    @classmethod
    async def execute(
        cls,
        client,
        prompt,
        model,
        seed=0,
        system_prompt="",
        detail="high",
        fps=1.0,
        reasoning_mode="auto",
        reasoning_effort="medium",
        turns=1,
        stream=False,
        file_expire_seconds=FILE_EXPIRE_DEFAULT_SECONDS,
    ) -> comfy_io.NodeOutput:
        if not (prompt or "").strip():
            raise BytePlusException(get_text("seed_llm_prompt_empty"))
        model = model or {}
        label = model.get("model")
        model_id = SEED_LLM_MODEL_MAP.get(label)
        if model_id is None:
            raise BytePlusException(get_text("seed_llm_unknown_model", model=label))
        temperature = model.get("temperature", 1.0)

        images = [t for t in (model.get("images") or {}).values() if t is not None]
        count = image_count(images)
        if count > SEED_LLM_MAX_IMAGES:
            raise BytePlusException(get_text("seed_llm_too_many_images", max=SEED_LLM_MAX_IMAGES, count=count))
        videos = [v for v in (model.get("videos") or {}).values() if v is not None]
        if len(videos) > SEED_LLM_MAX_VIDEOS:
            raise BytePlusException(
                get_text("seed_llm_too_many_videos", max=SEED_LLM_MAX_VIDEOS, count=len(videos))
            )

        node_id = cls.hidden.unique_id
        owner = _conversation_owner(client)
        turns = int(turns or 1)
        previous_response_id = None
        if turns > 1:
            last = SEED_LAST_RESPONSES.get(node_id)
            if last and last.get("owner") == owner:
                previous_response_id = last["id"]
        else:
            SEED_LAST_RESPONSES.pop(node_id, None)

        content = []
        if previous_response_id:
            log_msg("visual_cont_conv", id=previous_response_id)
        else:
            log_msg("visual_new_conv")
            expire_seconds = max(
                FILE_EXPIRE_MIN_SECONDS,
                min(int(file_expire_seconds or FILE_EXPIRE_DEFAULT_SECONDS), FILE_EXPIRE_MAX_SECONDS),
            )
            for path in await asyncio.to_thread(image_files, images):
                file_id = await upload_file_to_ark(client, path, expire_seconds=expire_seconds)
                content.append({"type": "input_image", "file_id": file_id, "detail": detail})
            for index, video in enumerate(videos, start=1):
                try:
                    path = await asyncio.to_thread(video_file, video)
                except Exception as e:
                    raise BytePlusException(get_text("seed_llm_video_convert_failed", index=index, e=e))
                file_id = await upload_file_to_ark(client, path, fps=fps, expire_seconds=expire_seconds)
                content.append({"type": "input_video", "file_id": file_id})
        content.append({"type": "input_text", "text": prompt})

        payload = build_seed_payload(
            model_id,
            content,
            temperature,
            system_prompt=system_prompt,
            reasoning_mode=reasoning_mode,
            reasoning_effort=reasoning_effort,
            supports_effort=label not in SEED_LLM_NO_REASONING_EFFORT,
            previous_response_id=previous_response_id,
            # Like core, a single-turn response is not stored; multi-turn needs it stored.
            store=False if turns <= 1 else None,
        )

        executor = BytePlusVisualExecutor(client)
        if stream:
            node_count = get_node_count_in_workflow("BytePlusSeed", prompt=cls.hidden.prompt)
            text, raw_json = await executor.stream_response_task(payload, is_single_node=node_count <= 1)
            try:
                response = json.loads(raw_json)
            except ValueError:
                response = {}
            response_text(response)  # raises on API errors and refusals
        else:
            task_id = await executor.create_response_task(payload)
            response = await executor.poll_response_result(task_id)
            text = response_text(response)
            raw_json = json.dumps(response, indent=2, ensure_ascii=False, default=str)

        if not text:
            raise BytePlusException(get_text("seed_llm_empty_response"))

        if turns > 1 and isinstance(response, dict) and response.get("id"):
            SEED_LAST_RESPONSES.pop(node_id, None)
            SEED_LAST_RESPONSES[node_id] = {"id": response["id"], "owner": owner}
            while len(SEED_LAST_RESPONSES) > SEED_LAST_RESPONSES_MAX:
                SEED_LAST_RESPONSES.pop(next(iter(SEED_LAST_RESPONSES)))
            log_msg("visual_cached_id", id=response["id"])

        return comfy_io.NodeOutput(text, raw_json)


# Registered in __init__.py.
NODES = [BytePlusSeed]
