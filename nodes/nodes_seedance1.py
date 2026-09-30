"""
Seedance 1.x nodes shaped like ComfyUI core's ByteDance Text to Video, Image to Video and First-Last-Frame to Video nodes.

Inputs, defaults, limits and tooltips copy core's ByteDanceTextToVideoNode,
ByteDanceImageToVideoNode and ByteDanceFirstLastFrameNode
(comfy_api_nodes/nodes_bytedance.py). Every node also takes this pack's API
Client as its first input and ends with this pack's extras (advanced). The
request goes straight to BytePlus ModelArk: parameters as JSON body fields,
frames as base64 data URIs (the Legacy Seedance 1.x request code).
"""
import json

from comfy_api.latest import io as comfy_io

from .constants import (
    DEFAULT_FILENAME_PREFIX,
    IMAGE_MAX_EDGE,
    IMAGE_MAX_RATIO,
    IMAGE_MIN_EDGE,
    IMAGE_MIN_RATIO,
    VIDEO_DEFAULT_TIMEOUT,
)
from .core_style import raise_if_output_linked, seed_input, video_extra_inputs, watermark_input
from .models_config import (
    SEEDANCE_1_5_PRO_MIN_DURATION,
    SEEDANCE_1_5_PRO_MODEL,
    SEEDANCE_1_DEFAULT_DURATION,
    SEEDANCE_1_DEFAULT_MODEL,
    SEEDANCE_1_FLF_DEFAULT_MODEL,
    SEEDANCE_1_FLF_MODEL_OPTIONS,
    SEEDANCE_1_IMAGE_RATIOS,
    SEEDANCE_1_MAX_DURATION,
    SEEDANCE_1_MIN_DURATION,
    SEEDANCE_1_MODEL_OPTIONS,
    SEEDANCE_1_MODELS,
    SEEDANCE_1_RESOLUTIONS,
    SEEDANCE_1_TEXT_RATIOS,
    SEEDANCE_DRAFT_RESOLUTION,
)
from .nodes_shared import (
    GLOBAL_CATEGORY,
    LOG_PREFIX,
    BytePlusClientType,
    BytePlusException,
    get_text,
)
from .nodes_video import BytePlusVideoBase, _raise_if_text_params, build_seedance1_frame_content

# Flags ModelArk would also read from the prompt text; here they are widgets
# (core's list plus the Legacy nodes' "dur", "frames" and "generate_audio").
FORBIDDEN_PROMPT_FLAGS = [
    "resolution",
    "ratio",
    "dur",
    "frames",
    "seed",
    "camerafixed",
    "watermark",
    "generate_audio",
]

# Output order: core's Video, then this pack's extras.
DRAFT_TASK_ID_OUTPUT = 1


def resolve_seedance1_model(model, options):
    """Core's option label -> BytePlus model ID, limited to the node's options."""
    if model not in options or model not in SEEDANCE_1_MODELS:
        raise BytePlusException(get_text("err_model_not_supported", model=model))
    return SEEDANCE_1_MODELS[model]


def validate_seedance1_prompt(prompt):
    """Core's validate_string(prompt, strip_whitespace=True, min_length=1)."""
    if not str(prompt or "").strip():
        raise BytePlusException(get_text("err_seedance1_prompt_empty"))


def validate_seedance1_duration(model, duration, auto_duration=False):
    """Core: Seedance 1.5 Pro needs at least 4 seconds."""
    if (
        model == SEEDANCE_1_5_PRO_MODEL
        and not auto_duration
        and duration < SEEDANCE_1_5_PRO_MIN_DURATION
    ):
        raise BytePlusException(
            get_text(
                "err_seedance1_min_duration",
                min=SEEDANCE_1_5_PRO_MIN_DURATION,
                duration=duration,
            )
        )


def validate_seedance1_frame(helper, name, image):
    """
    Core's frame checks: 300-6000 px per side, then aspect ratio 0.4-2.5
    (both inclusive).
    """
    if image is None:
        raise BytePlusException(get_text("err_seedance1_image_missing", name=name))
    width, height = helper._extract_image_hw(image)
    if not (
        IMAGE_MIN_EDGE <= width <= IMAGE_MAX_EDGE
        and IMAGE_MIN_EDGE <= height <= IMAGE_MAX_EDGE
    ):
        raise BytePlusException(
            get_text(
                "err_seedance1_image_size",
                name=name,
                min=IMAGE_MIN_EDGE,
                max=IMAGE_MAX_EDGE,
                width=width,
                height=height,
            )
        )
    ratio = float(width) / float(height)
    if not (IMAGE_MIN_RATIO <= ratio <= IMAGE_MAX_RATIO):
        raise BytePlusException(
            get_text(
                "err_seedance1_image_ratio",
                name=name,
                min=IMAGE_MIN_RATIO,
                max=IMAGE_MAX_RATIO,
                ratio=f"{ratio:.4f}",
            )
        )


def draft_task_ids_from_response(response):
    """Task IDs of the finished tasks in the response JSON, one per line."""
    try:
        items = json.loads(response) if response else None
    except (TypeError, ValueError):
        return ""
    if not isinstance(items, list):
        # A pending non_blocking run or an ignored failure.
        return ""
    return "\n".join(
        str(item["id"]) for item in items if isinstance(item, dict) and item.get("id")
    )


def _message_without_prefix(key):
    text = get_text(key)
    return text[len(LOG_PREFIX):] if text.startswith(LOG_PREFIX) else text


# --- Inputs (core's definitions) ---

def _model_input(options, default):
    return comfy_io.Combo.Input("model", options=list(options), default=default)


def _prompt_input():
    return comfy_io.String.Input(
        "prompt",
        multiline=True,
        tooltip="The text prompt used to generate the video.",
    )


def _resolution_input():
    return comfy_io.Combo.Input(
        "resolution",
        options=list(SEEDANCE_1_RESOLUTIONS),
        tooltip="The resolution of the output video.",
    )


def _aspect_ratio_input(options):
    return comfy_io.Combo.Input(
        "aspect_ratio",
        options=list(options),
        tooltip="The aspect ratio of the output video.",
    )


def _duration_input():
    return comfy_io.Int.Input(
        "duration",
        default=SEEDANCE_1_DEFAULT_DURATION,
        min=SEEDANCE_1_MIN_DURATION,
        max=SEEDANCE_1_MAX_DURATION,
        step=1,
        tooltip="The duration of the output video in seconds.",
        display_mode=comfy_io.NumberDisplay.slider,
    )


def _core_option_inputs():
    return [
        seed_input(tooltip="Seed to use for generation.", optional=True),
        comfy_io.Boolean.Input(
            "camera_fixed",
            default=False,
            tooltip="Specifies whether to fix the camera. The platform appends an instruction "
            "to fix the camera to your prompt, but does not guarantee the actual effect.",
            optional=True,
            advanced=True,
        ),
        watermark_input(
            tooltip='Whether to add an "AI generated" watermark to the video.',
            optional=True,
        ),
        comfy_io.Boolean.Input(
            "generate_audio",
            default=False,
            tooltip="This parameter is ignored for any model except seedance-1-5-pro.",
            optional=True,
            advanced=True,
        ),
    ]


def _extra_inputs():
    """This pack's extras, after core's inputs."""
    extras = [
        comfy_io.Boolean.Input(
            "auto_duration",
            default=False,
            tooltip="seedance-1-5-pro only: let the model choose a length of 4 to 12 seconds; "
            "duration is ignored.",
            advanced=True,
        ),
        comfy_io.Boolean.Input(
            "draft_mode",
            default=False,
            tooltip="seedance-1-5-pro only: generate a low-cost 480p draft (no offline inference). "
            "Connect draft_task_id to the Seedance Draft to Final node to render the final "
            "video from it; drafts can be rendered for 7 days.",
            advanced=True,
        ),
        *video_extra_inputs(include_offline=True),
    ]
    # Core's seed/camera_fixed/watermark/generate_audio are optional, and the
    # frontend lists required inputs before optional ones: keep the extras
    # optional too so they stay last.
    for item in extras:
        item.optional = True
    return extras


def _outputs():
    return [
        comfy_io.Video.Output(),
        comfy_io.String.Output(
            "draft_task_id",
            tooltip="Task ID of a draft_mode run (one per line when generation_count > 1); empty "
            "otherwise. Connect it to the Seedance Draft to Final node to render the final video.",
        ),
        comfy_io.Image.Output("last_frame", tooltip="Last frame of the generated video."),
        comfy_io.String.Output(
            "response",
            tooltip="Task results as JSON, or the pending task IDs of a non_blocking run.",
        ),
    ]


def _schema(node_id, display_name, description, model_options, default_model, frame_inputs, ratios):
    return comfy_io.Schema(
        node_id=node_id,
        display_name=display_name,
        category=GLOBAL_CATEGORY,
        description=description,
        inputs=[
            BytePlusClientType.Input("client"),
            _model_input(model_options, default_model),
            _prompt_input(),
            *frame_inputs,
            _resolution_input(),
            _aspect_ratio_input(ratios),
            _duration_input(),
            *_core_option_inputs(),
            *_extra_inputs(),
        ],
        outputs=_outputs(),
        hidden=[comfy_io.Hidden.unique_id, comfy_io.Hidden.prompt],
        is_output_node=True,
    )


async def generate_seedance1_video(
    cls,
    *,
    client,
    model,
    model_options,
    prompt,
    frames,
    resolution,
    aspect_ratio,
    duration,
    seed,
    camera_fixed,
    watermark,
    generate_audio,
    auto_duration,
    draft_mode,
    enable_offline_inference,
    generation_count,
    filename_prefix,
    save_last_frame_batch,
    non_blocking,
):
    """
    Shared execute for the three nodes. `frames` is a list of (input name,
    image) for first_frame and, optionally, last_frame.
    """
    model_id = resolve_seedance1_model(model, model_options)
    is_1_5_pro = model == SEEDANCE_1_5_PRO_MODEL
    for option, enabled in (("auto_duration", auto_duration), ("draft_mode", draft_mode)):
        if enabled and not is_1_5_pro:
            raise BytePlusException(
                get_text("err_seedance1_1_5_only", option=option, model=SEEDANCE_1_5_PRO_MODEL)
            )
    if not draft_mode:
        raise_if_output_linked(
            cls,
            DRAFT_TASK_ID_OUTPUT,
            _message_without_prefix("err_seedance1_draft_output_linked"),
        )

    validate_seedance1_duration(model, duration, auto_duration)
    validate_seedance1_prompt(prompt)
    _raise_if_text_params(prompt, FORBIDDEN_PROMPT_FLAGS)

    helper = BytePlusVideoBase()
    for name, image in frames:
        validate_seedance1_frame(helper, name, image)
    images = [image for _name, image in frames]
    first_frame = images[0] if images else None
    last_frame = images[1] if len(images) > 1 else None
    content = build_seedance1_frame_content(helper, first_frame, last_frame)

    service_tier, execution_expires_after = helper._get_service_options(
        enable_offline_inference, VIDEO_DEFAULT_TIMEOUT
    )
    extra_api_params = {"camera_fixed": camera_fixed, "watermark": watermark}
    if is_1_5_pro:
        extra_api_params["generate_audio"] = generate_audio
    return_last_frame = True
    if draft_mode:
        # Drafts are 480p only, without last frame or offline inference.
        extra_api_params["draft"] = True
        resolution = SEEDANCE_DRAFT_RESOLUTION
        return_last_frame = False
        service_tier = "default"
        save_last_frame_batch = False

    result = await helper._common_generation_logic(
        client,
        prompt,
        duration,
        resolution,
        aspect_ratio,
        seed,
        generation_count,
        filename_prefix,
        save_last_frame_batch,
        non_blocking,
        cls.hidden.unique_id,
        model_name=model_id,
        content=content,
        forbidden_params=FORBIDDEN_PROMPT_FLAGS,
        service_tier=service_tier,
        execution_expires_after=execution_expires_after,
        is_auto_duration=bool(auto_duration),
        extra_api_params=extra_api_params,
        return_last_frame=return_last_frame,
        node_class_type=cls.NODE_ID,
        workflow_prompt=cls.hidden.prompt,
    )

    video, last_frame_image, response = (tuple(result.args) + (None, None, None))[:3]
    draft_task_id = draft_task_ids_from_response(response) if draft_mode else ""
    return comfy_io.NodeOutput(video, draft_task_id, last_frame_image, response)


class BytePlusSeedanceTextToVideo(comfy_io.ComfyNode):
    """Core's ByteDance Text to Video, on BytePlus ModelArk."""

    NODE_ID = "BytePlusSeedanceTextToVideo"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return _schema(
            cls.NODE_ID,
            "BytePlus Seedance Text to Video",
            "Generate video using Seedance models on BytePlus ModelArk based on prompt",
            SEEDANCE_1_MODEL_OPTIONS,
            SEEDANCE_1_DEFAULT_MODEL,
            [],
            SEEDANCE_1_TEXT_RATIOS,
        )

    @classmethod
    async def execute(
        cls,
        client,
        model,
        prompt,
        resolution,
        aspect_ratio,
        duration,
        seed=0,
        camera_fixed=False,
        watermark=False,
        generate_audio=False,
        auto_duration=False,
        draft_mode=False,
        enable_offline_inference=False,
        generation_count=1,
        filename_prefix=DEFAULT_FILENAME_PREFIX,
        save_last_frame_batch=False,
        non_blocking=False,
    ) -> comfy_io.NodeOutput:
        return await generate_seedance1_video(
            cls,
            client=client,
            model=model,
            model_options=SEEDANCE_1_MODEL_OPTIONS,
            prompt=prompt,
            frames=[],
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration,
            seed=seed,
            camera_fixed=camera_fixed,
            watermark=watermark,
            generate_audio=generate_audio,
            auto_duration=auto_duration,
            draft_mode=draft_mode,
            enable_offline_inference=enable_offline_inference,
            generation_count=generation_count,
            filename_prefix=filename_prefix,
            save_last_frame_batch=save_last_frame_batch,
            non_blocking=non_blocking,
        )


class BytePlusSeedanceImageToVideo(comfy_io.ComfyNode):
    """Core's ByteDance Image to Video, on BytePlus ModelArk."""

    NODE_ID = "BytePlusSeedanceImageToVideo"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return _schema(
            cls.NODE_ID,
            "BytePlus Seedance Image to Video",
            "Generate video using Seedance models on BytePlus ModelArk based on image and prompt",
            SEEDANCE_1_MODEL_OPTIONS,
            SEEDANCE_1_DEFAULT_MODEL,
            [
                comfy_io.Image.Input(
                    "image",
                    tooltip="First frame to be used for the video.",
                ),
            ],
            SEEDANCE_1_IMAGE_RATIOS,
        )

    @classmethod
    async def execute(
        cls,
        client,
        model,
        prompt,
        image,
        resolution,
        aspect_ratio,
        duration,
        seed=0,
        camera_fixed=False,
        watermark=False,
        generate_audio=False,
        auto_duration=False,
        draft_mode=False,
        enable_offline_inference=False,
        generation_count=1,
        filename_prefix=DEFAULT_FILENAME_PREFIX,
        save_last_frame_batch=False,
        non_blocking=False,
    ) -> comfy_io.NodeOutput:
        return await generate_seedance1_video(
            cls,
            client=client,
            model=model,
            model_options=SEEDANCE_1_MODEL_OPTIONS,
            prompt=prompt,
            frames=[("image", image)],
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration,
            seed=seed,
            camera_fixed=camera_fixed,
            watermark=watermark,
            generate_audio=generate_audio,
            auto_duration=auto_duration,
            draft_mode=draft_mode,
            enable_offline_inference=enable_offline_inference,
            generation_count=generation_count,
            filename_prefix=filename_prefix,
            save_last_frame_batch=save_last_frame_batch,
            non_blocking=non_blocking,
        )


class BytePlusSeedanceFirstLastFrame(comfy_io.ComfyNode):
    """Core's ByteDance First-Last-Frame to Video, on BytePlus ModelArk."""

    NODE_ID = "BytePlusSeedanceFirstLastFrame"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return _schema(
            cls.NODE_ID,
            "BytePlus Seedance First-Last-Frame to Video",
            "Generate video using prompt and first and last frames.",
            SEEDANCE_1_FLF_MODEL_OPTIONS,
            SEEDANCE_1_FLF_DEFAULT_MODEL,
            [
                comfy_io.Image.Input(
                    "first_frame",
                    tooltip="First frame to be used for the video.",
                ),
                comfy_io.Image.Input(
                    "last_frame",
                    tooltip="Last frame to be used for the video.",
                ),
            ],
            SEEDANCE_1_IMAGE_RATIOS,
        )

    @classmethod
    async def execute(
        cls,
        client,
        model,
        prompt,
        first_frame,
        last_frame,
        resolution,
        aspect_ratio,
        duration,
        seed=0,
        camera_fixed=False,
        watermark=False,
        generate_audio=False,
        auto_duration=False,
        draft_mode=False,
        enable_offline_inference=False,
        generation_count=1,
        filename_prefix=DEFAULT_FILENAME_PREFIX,
        save_last_frame_batch=False,
        non_blocking=False,
    ) -> comfy_io.NodeOutput:
        return await generate_seedance1_video(
            cls,
            client=client,
            model=model,
            model_options=SEEDANCE_1_FLF_MODEL_OPTIONS,
            prompt=prompt,
            frames=[("first_frame", first_frame), ("last_frame", last_frame)],
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration,
            seed=seed,
            camera_fixed=camera_fixed,
            watermark=watermark,
            generate_audio=generate_audio,
            auto_duration=auto_duration,
            draft_mode=draft_mode,
            enable_offline_inference=enable_offline_inference,
            generation_count=generation_count,
            filename_prefix=filename_prefix,
            save_last_frame_batch=save_last_frame_batch,
            non_blocking=non_blocking,
        )


# Registered in __init__.py.
NODES = [
    BytePlusSeedanceTextToVideo,
    BytePlusSeedanceImageToVideo,
    BytePlusSeedanceFirstLastFrame,
]
